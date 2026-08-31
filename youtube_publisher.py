"""YouTube OAuth, metadata helpers, and resumable uploads for CutLab AI.

Google dependencies are imported lazily so CutLab can still start before the
optional YouTube packages are installed.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional
from urllib.parse import urlencode, urlparse
from urllib.request import Request as UrlRequest
from urllib.request import urlopen


YOUTUBE_UPLOAD_SCOPE = (
    "https://www.googleapis.com/auth/youtube.upload"
)


def _is_local_http_uri(uri: str) -> bool:
    """Return True only for an HTTP callback on the local loopback host."""
    try:
        parsed = urlparse(uri)
    except Exception:
        return False

    return (
        parsed.scheme.lower() == "http"
        and (parsed.hostname or "").lower()
        in {"127.0.0.1", "localhost", "::1"}
    )


class YouTubeError(RuntimeError):
    """Base error exposed to the CutLab API."""


class YouTubeDependencyError(YouTubeError):
    """Raised when the optional Google client packages are missing."""


class YouTubeConfigurationError(YouTubeError):
    """Raised when OAuth credentials have not been configured."""


class YouTubeAuthenticationError(YouTubeError):
    """Raised when the saved Google authorization is invalid."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(
    path: Path,
    payload: Dict,
    *,
    private: bool = False,
) -> None:
    path.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    temporary = path.with_name(
        f"{path.name}.tmp"
    )

    temporary.write_text(
        json.dumps(
            payload,
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    if private:
        try:
            os.chmod(
                temporary,
                0o600,
            )
        except OSError:
            pass

    temporary.replace(path)


def sidecar_path(video_path: Path) -> Path:
    """Return the private CutLab data file associated with one rendered MP4."""

    return video_path.with_name(
        f"{video_path.name}.cutlab.json"
    )


def load_sidecar(video_path: Path) -> Dict:
    path = sidecar_path(video_path)

    if not path.exists():
        return {}

    try:
        payload = json.loads(
            path.read_text(
                encoding="utf-8",
            )
        )
    except Exception:
        return {}

    return (
        payload
        if isinstance(payload, dict)
        else {}
    )


def save_sidecar(
    video_path: Path,
    payload: Dict,
) -> Dict:
    data = dict(payload)
    data["version"] = 1
    data["updated_at"] = _utc_now()

    _atomic_json_write(
        sidecar_path(video_path),
        data,
        private=True,
    )

    return data


def parse_json_loose(raw: str) -> Dict:
    """Extract a JSON object even when an LLM adds markdown or prose."""

    text = str(raw or "").strip()
    text = re.sub(
        r"^```(?:json)?\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s*```$",
        "",
        text,
    )

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        candidates = []

        for match in re.finditer(
            r"\{",
            text,
        ):
            try:
                candidate, _ = decoder.raw_decode(
                    text[match.start():]
                )
            except json.JSONDecodeError:
                continue

            if isinstance(candidate, dict):
                candidates.append(candidate)

        if candidates:
            parsed = next(
                (
                    candidate
                    for candidate in candidates
                    if isinstance(
                        candidate.get("items"),
                        list,
                    )
                ),
                candidates[0],
            )
        else:
            # Some OpenAI-compatible models occasionally emit an otherwise
            # correct object with a missing quote, comma, or closing brace.
            # Repair only after strict parsing and raw object extraction fail.
            try:
                from json_repair import repair_json

                repaired = repair_json(
                    text,
                    return_objects=True,
                )
            except Exception as exc:
                raise YouTubeError(
                    "A IA nÃ£o retornou metadados em JSON vÃ¡lido."
                ) from exc

            if not isinstance(repaired, dict):
                raise YouTubeError(
                    "A IA nÃ£o retornou metadados em JSON vÃ¡lido."
                )

            parsed = repaired

    if not isinstance(parsed, dict):
        raise YouTubeError(
            "A IA retornou um formato de metadados invÃ¡lido."
        )

    return parsed


def _clean_title(
    value: object,
    fallback: str,
) -> str:
    title = re.sub(
        r"\s+",
        " ",
        str(value or "").strip().strip('"\''),
    )
    title = title.replace(
        "<",
        "",
    ).replace(
        ">",
        "",
    ).strip()

    if not title:
        title = re.sub(
            r"\s+",
            " ",
            str(fallback or "Short do CutLab").strip(),
        )

    if len(title) > 100:
        title = title[:100].rsplit(
            " ",
            1,
        )[0].rstrip(
            "-â€“â€”:,. "
        )

    return title or "Short do CutLab"


def _clean_description(value: object) -> str:
    description = str(value or "").strip()
    description = description.replace(
        "\x00",
        "",
    )

    if not description:
        description = (
            "Confira este destaque e compartilhe sua opiniÃ£o nos comentÃ¡rios."
        )

    if not re.search(
        r"(?i)(?:^|\s)#shorts\b",
        description,
    ):
        description = (
            f"{description.rstrip()}\n\n#Shorts"
        )

    return description[:5000].rstrip()


def _clean_tags(
    value: object,
    fallback_title: str = "",
) -> List[str]:
    if isinstance(value, str):
        candidates = re.split(
            r"[,;\n]+",
            value,
        )
    elif isinstance(value, list):
        candidates = value
    else:
        candidates = []

    tags: List[str] = []
    seen = set()
    total_length = 0

    for candidate in candidates:
        tag = re.sub(
            r"\s+",
            " ",
            str(candidate or "")
            .strip()
            .lstrip("#"),
        )

        if not tag:
            continue

        tag = tag[:60].strip()
        key = tag.casefold()

        if key in seen:
            continue

        projected = (
            total_length
            + len(tag)
            + (1 if tags else 0)
        )

        if projected > 480:
            break

        seen.add(key)
        tags.append(tag)
        total_length = projected

        if len(tags) >= 15:
            break

    # Models sometimes return only 4-6 good tags even when asked for more.
    # Complete the set with terms already present in the title, so metadata
    # remains useful without inventing subjects or spending another API call.
    title = re.sub(
        r"\s+",
        " ",
        str(fallback_title or "").strip(),
    )
    stopwords = {
        "para", "como", "com", "sem", "uma", "uns", "das", "dos",
        "que", "seu", "sua", "seus", "suas", "sobre", "entre",
    }
    title_terms = [
        title[:60].strip(),
        *re.findall(r"[^\W\d_]{4,}", title, flags=re.UNICODE),
    ]

    for candidate in title_terms:
        tag = str(candidate or "").strip().lstrip("#")
        key = tag.casefold()
        if (
            not tag
            or key in seen
            or key in stopwords
            or len(tags) >= 8
        ):
            continue

        projected = total_length + len(tag) + (1 if tags else 0)
        if projected > 480:
            break

        seen.add(key)
        tags.append(tag)
        total_length = projected

    if "shorts" not in seen:
        tags.append("shorts")

    return tags[:15]



def metadata_is_complete(
    metadata: Dict,
) -> bool:
    """Return True only when AI metadata is useful enough for publishing."""

    title = str(
        metadata.get(
            "title",
            "",
        )
        or ""
    ).strip()

    description = str(
        metadata.get(
            "description",
            "",
        )
        or ""
    ).strip()

    tags = metadata.get(
        "tags",
        [],
    )

    if not isinstance(
        tags,
        list,
    ):
        return False

    useful_tags = [
        str(tag).strip()
        for tag in tags
        if str(tag).strip()
    ]

    generic_descriptions = {
        "Confira este destaque e compartilhe sua opiniÃ£o nos comentÃ¡rios.",
        "Confira este destaque e compartilhe sua opiniÃ£o nos comentÃ¡rios.\n\n#Shorts",
    }

    if (
        len(title) < 12
        or len(description) < 120
        or description in generic_descriptions
        or len(useful_tags) < 8
    ):
        return False

    return True


def sanitize_metadata(
    value: object,
    *,
    fallback_title: str,
) -> Dict:
    item = (
        value
        if isinstance(value, dict)
        else {}
    )

    return {
        "title": _clean_title(
            item.get("title"),
            fallback_title,
        ),
        "description": _clean_description(
            item.get("description")
        ),
        "tags": _clean_tags(
            item.get("tags"),
            fallback_title,
        ),
        "generated_at": _utc_now(),
    }


def extract_transcript_excerpt(
    segments: object,
    start_time: float,
    end_time: float,
    *,
    max_chars: int = 3500,
) -> str:
    if not isinstance(segments, list):
        return ""

    parts: List[str] = []

    for segment in segments:
        if not isinstance(segment, dict):
            continue

        try:
            start = float(
                segment.get("start", 0)
            )
            end = float(
                segment.get("end", start)
            )
        except (TypeError, ValueError):
            continue

        if (
            end <= start_time
            or start >= end_time
        ):
            continue

        text = re.sub(
            r"\s+",
            " ",
            str(
                segment.get("text", "")
            ).strip(),
        )

        if text:
            parts.append(text)

    excerpt = " ".join(parts)
    return excerpt[:max_chars].strip()


def build_metadata_prompt(
    contexts: List[Dict],
) -> str:
    compact_contexts = []

    for index, context in enumerate(
        contexts,
        1,
    ):
        compact_contexts.append(
            {
                "index": index,
                "filename": context.get(
                    "filename",
                    "",
                ),
                "suggested_title": context.get(
                    "highlight_title",
                    "",
                ),
                "hook": context.get(
                    "hook_sentence",
                    "",
                ),
                "viral_reason": context.get(
                    "virality_reason",
                    "",
                ),
                "transcript": str(
                    context.get(
                        "transcript",
                        "",
                    )
                )[:3500],
            }
        )

    source = json.dumps(
        compact_contexts,
        ensure_ascii=False,
    )

    return f"""VocÃª Ã© estrategista de YouTube Shorts para pÃºblico brasileiro.
Crie metadados fiÃ©is ao conteÃºdo de cada corte abaixo.

Regras obrigatÃ³rias:
- Escreva em portuguÃªs do Brasil.
- NÃ£o invente fatos, nomes, nÃºmeros ou promessas ausentes na transcriÃ§Ã£o.
- TÃ­tulo com no mÃ¡ximo 100 caracteres; prefira 45 a 70, claro e curioso.
- NÃ£o use tÃ­tulo em CAIXA ALTA inteira e nÃ£o comece com emoji.
- DescriÃ§Ã£o completa com 2 parÃ¡grafos curtos e informativos, entre 180 e 500 caracteres no total.
- A descriÃ§Ã£o deve explicar o assunto do corte, trazer contexto suficiente e terminar com uma chamada natural para comentÃ¡rio.
- Inclua de 3 a 5 hashtags relevantes no fim da descriÃ§Ã£o, obrigatoriamente incluindo #Shorts.
- Gere de 8 a 15 tags sem #, especÃ­ficas ao assunto do vÃ­deo. Evite tags genÃ©ricas repetidas.
- NÃƒO deixe description vazia, genÃ©rica ou com apenas uma frase curta.
- NÃƒO retorne somente "shorts" em tags.
- Cada item deve preservar exatamente filename e index recebidos.

Retorne SOMENTE JSON vÃ¡lido neste formato:
{{"items":[{{"index":1,"filename":"arquivo.mp4","title":"...","description":"...","tags":["tag 1","tag 2"]}}]}}

Cortes:
{source}
"""


def apply_generated_metadata(
    contexts: List[Dict],
    raw_response: str,
) -> Dict[str, Dict]:
    parsed = parse_json_loose(
        raw_response
    )
    raw_items = parsed.get(
        "items",
        [],
    )

    if not isinstance(raw_items, list):
        raise YouTubeError(
            "A IA nÃ£o retornou a lista de metadados esperada."
        )

    by_filename = {}
    by_index = {}

    for item in raw_items:
        if not isinstance(item, dict):
            continue

        filename = str(
            item.get("filename", "")
        ).strip()

        if filename:
            by_filename[filename] = item

        try:
            index = int(
                item.get("index", 0)
            )
        except (TypeError, ValueError):
            index = 0

        if index > 0:
            by_index[index] = item

    generated: Dict[str, Dict] = {}

    for index, context in enumerate(
        contexts,
        1,
    ):
        filename = str(
            context.get("filename", "")
        )

        raw_item = (
            by_filename.get(filename)
            or by_index.get(index)
            or {}
        )

        generated[filename] = sanitize_metadata(
            raw_item,
            fallback_title=str(
                context.get(
                    "highlight_title",
                    "Short do CutLab",
                )
            ),
        )

    return generated


class YouTubeManager:
    """Manage one local YouTube OAuth connection for CutLab AI."""

    def __init__(self, root: Path):
        self.root = Path(root).resolve()
        self.config_dir = (
            self.root
            / ".cutlab"
        )
        self.config_dir.mkdir(
            parents=True,
            exist_ok=True,
        )

        configured_secret = os.getenv(
            "YOUTUBE_CLIENT_SECRETS_FILE",
            "",
        ).strip()

        if configured_secret:
            secret_path = Path(
                configured_secret
            )

            if not secret_path.is_absolute():
                secret_path = (
                    self.root
                    / secret_path
                )

            self.client_secret_path = (
                secret_path.resolve()
            )
        else:
            self.client_secret_path = (
                self.config_dir
                / "youtube_client_secret.json"
            )

        self.token_path = (
            self.config_dir
            / "youtube_token.json"
        )

        self._flows: Dict[str, tuple] = {}
        self._flow_lock = threading.Lock()

    @staticmethod
    def _imports():
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import Flow
            from googleapiclient.discovery import build
            from googleapiclient.http import MediaFileUpload
        except ImportError as exc:
            raise YouTubeDependencyError(
                "DependÃªncias do YouTube ausentes. Rode o instalador desta atualizaÃ§Ã£o."
            ) from exc

        return {
            "Request": Request,
            "Credentials": Credentials,
            "Flow": Flow,
            "build": build,
            "MediaFileUpload": MediaFileUpload,
        }

    def dependencies_available(self) -> bool:
        try:
            self._imports()
            return True
        except YouTubeDependencyError:
            return False

    def client_configured(self) -> bool:
        return (
            self.client_secret_path.exists()
            and self.client_secret_path.is_file()
        )

    def save_client_secret(
        self,
        payload: object,
    ) -> None:
        if not isinstance(payload, dict):
            raise YouTubeConfigurationError(
                "O arquivo de credenciais nÃ£o contÃ©m JSON vÃ¡lido."
            )

        application = (
            payload.get("web")
            or payload.get("installed")
        )

        if not isinstance(application, dict):
            raise YouTubeConfigurationError(
                "Use o JSON de um cliente OAuth do Google."
            )

        required = {
            "client_id",
            "client_secret",
            "auth_uri",
            "token_uri",
        }

        if not required.issubset(
            application.keys()
        ):
            raise YouTubeConfigurationError(
                "O JSON OAuth estÃ¡ incompleto. Baixe-o novamente no Google Cloud."
            )

        _atomic_json_write(
            self.client_secret_path,
            payload,
            private=True,
        )

        # A credential from another OAuth client cannot be reused safely.
        if self.token_path.exists():
            try:
                self.token_path.unlink()
            except OSError:
                pass

    def _load_credentials(
        self,
        *,
        refresh: bool,
    ):
        imports = self._imports()

        if not self.token_path.exists():
            raise YouTubeAuthenticationError(
                "Conecte seu canal do YouTube primeiro."
            )

        try:
            credentials = imports[
                "Credentials"
            ].from_authorized_user_file(
                str(self.token_path),
                [YOUTUBE_UPLOAD_SCOPE],
            )
        except Exception as exc:
            raise YouTubeAuthenticationError(
                "A autorizaÃ§Ã£o salva do YouTube Ã© invÃ¡lida. Conecte o canal novamente."
            ) from exc

        if (
            refresh
            and credentials.expired
            and credentials.refresh_token
        ):
            try:
                credentials.refresh(
                    imports["Request"]()
                )
                self._save_token(
                    credentials
                )
            except Exception as exc:
                raise YouTubeAuthenticationError(
                    "A autorizaÃ§Ã£o do YouTube expirou. Conecte o canal novamente."
                ) from exc

        if refresh and not credentials.valid:
            raise YouTubeAuthenticationError(
                "A autorizaÃ§Ã£o do YouTube nÃ£o estÃ¡ vÃ¡lida. Conecte novamente."
            )

        return credentials

    def _save_token(self, credentials) -> None:
        try:
            payload = json.loads(
                credentials.to_json()
            )
        except Exception as exc:
            raise YouTubeAuthenticationError(
                "NÃ£o foi possÃ­vel salvar a autorizaÃ§Ã£o do YouTube."
            ) from exc

        _atomic_json_write(
            self.token_path,
            payload,
            private=True,
        )

    def status(self) -> Dict:
        dependencies = (
            self.dependencies_available()
        )
        connected = False
        needs_reconnect = False

        if dependencies and self.token_path.exists():
            try:
                credentials = self._load_credentials(
                    refresh=False,
                )
                connected = bool(
                    credentials.valid
                    or credentials.refresh_token
                )
            except YouTubeError:
                needs_reconnect = True

        return {
            "dependencies": dependencies,
            "client_configured": self.client_configured(),
            "connected": connected,
            "needs_reconnect": needs_reconnect,
        }

    def begin_auth(
        self,
        redirect_uri: str,
    ) -> Dict:
        imports = self._imports()

        if not self.client_configured():
            raise YouTubeConfigurationError(
                "Importe primeiro o arquivo JSON OAuth do Google Cloud."
            )

        flow = imports[
            "Flow"
        ].from_client_secrets_file(
            str(self.client_secret_path),
            scopes=[YOUTUBE_UPLOAD_SCOPE],
        )
        flow.redirect_uri = redirect_uri

        authorization_url, state = (
            flow.authorization_url(
                access_type="offline",
                include_granted_scopes="true",
                prompt="consent",
            )
        )

        now = time.monotonic()

        with self._flow_lock:
            self._flows = {
                key: value
                for key, value in self._flows.items()
                if now - value[1] < 900
            }
            self._flows[state] = (
                flow,
                now,
            )

        return {
            "authorization_url": authorization_url,
            "state": state,
            "redirect_uri": redirect_uri,
        }

    def finish_auth(
        self,
        state: str,
        authorization_response: str,
    ) -> Dict:
        with self._flow_lock:
            stored = self._flows.pop(
                state,
                None,
            )

        if not stored:
            raise YouTubeAuthenticationError(
                "Esta tentativa de conexÃ£o expirou. Tente conectar novamente."
            )

        flow = stored[0]

        # OAuthLib correctly requires HTTPS for remote OAuth callbacks. CutLab
        # runs only on the local loopback interface, where Google explicitly
        # permits an HTTP redirect URI. Temporarily relax the transport check
        # for this one local token exchange and restore the previous setting
        # immediately afterwards.
        allow_local_http = _is_local_http_uri(
            authorization_response
        )
        previous_insecure_transport = os.environ.get(
            "OAUTHLIB_INSECURE_TRANSPORT"
        )

        if allow_local_http:
            os.environ[
                "OAUTHLIB_INSECURE_TRANSPORT"
            ] = "1"

        try:
            flow.fetch_token(
                authorization_response=authorization_response
            )
        except Exception as exc:
            technical_detail = (
                f"{type(exc).__name__}: {exc}"
            )
            raise YouTubeAuthenticationError(
                "O Google nÃ£o concluiu a autorizaÃ§Ã£o do canal. "
                f"Detalhe tÃ©cnico: {technical_detail}"
            ) from exc
        finally:
            if allow_local_http:
                if previous_insecure_transport is None:
                    os.environ.pop(
                        "OAUTHLIB_INSECURE_TRANSPORT",
                        None,
                    )
                else:
                    os.environ[
                        "OAUTHLIB_INSECURE_TRANSPORT"
                    ] = previous_insecure_transport

        self._save_token(
            flow.credentials
        )

        return self.status()

    def disconnect(self) -> None:
        token = ""

        if self.token_path.exists():
            try:
                saved = json.loads(
                    self.token_path.read_text(
                        encoding="utf-8",
                    )
                )

                if isinstance(saved, dict):
                    token = str(
                        saved.get("refresh_token")
                        or saved.get("token")
                        or ""
                    ).strip()
            except Exception:
                token = ""

        # Revoke remotely when possible, then always remove the local grant.
        # A network failure must not prevent the user from disconnecting this
        # CutLab installation.
        if token:
            try:
                payload = urlencode(
                    {"token": token}
                ).encode("utf-8")
                revoke_request = UrlRequest(
                    "https://oauth2.googleapis.com/revoke",
                    data=payload,
                    headers={
                        "Content-Type": (
                            "application/x-www-form-urlencoded"
                        ),
                    },
                    method="POST",
                )

                with urlopen(
                    revoke_request,
                    timeout=10,
                ) as response:
                    response.read()
            except Exception:
                pass

        if self.token_path.exists():
            try:
                self.token_path.unlink()
            except OSError as exc:
                raise YouTubeAuthenticationError(
                    "NÃ£o foi possÃ­vel remover a autorizaÃ§Ã£o salva."
                ) from exc

        with self._flow_lock:
            self._flows.clear()

    def upload_video(
        self,
        video_path: Path,
        *,
        title: str,
        description: str,
        tags: List[str],
        privacy_status: str,
        made_for_kids: bool,
        notify_subscribers: bool,
        publish_at: Optional[str] = None,
        progress_callback: Optional[
            Callable[[float], None]
        ] = None,
    ) -> Dict:
        imports = self._imports()
        credentials = self._load_credentials(
            refresh=True,
        )

        if privacy_status not in {
            "private",
            "unlisted",
            "public",
        }:
            raise YouTubeError(
                "Privacidade do YouTube invÃ¡lida."
            )

        if not video_path.exists():
            raise YouTubeError(
                "O arquivo do Short nÃ£o foi encontrado."
            )

        metadata = sanitize_metadata(
            {
                "title": title,
                "description": description,
                "tags": tags,
            },
            fallback_title=video_path.stem,
        )

        service = imports["build"](
            "youtube",
            "v3",
            credentials=credentials,
            cache_discovery=False,
        )

        media = imports[
            "MediaFileUpload"
        ](
            str(video_path),
            mimetype="video/mp4",
            chunksize=8 * 1024 * 1024,
            resumable=True,
        )

        publish_at = str(
            publish_at
            or ""
        ).strip()

        if publish_at:
            privacy_status = "private"

        status_body = {
            "privacyStatus": privacy_status,
            "selfDeclaredMadeForKids": bool(
                made_for_kids
            ),
        }

        if publish_at:
            status_body["publishAt"] = publish_at

        body = {
            "snippet": {
                "title": metadata["title"],
                "description": metadata["description"],
                "tags": metadata["tags"],
                "categoryId": "22",
            },
            "status": status_body,
        }

        request = service.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media,
            notifySubscribers=bool(
                notify_subscribers
            ),
        )

        response = None

        try:
            while response is None:
                upload_status, response = (
                    request.next_chunk(
                        num_retries=3
                    )
                )

                if (
                    upload_status is not None
                    and progress_callback
                ):
                    progress_callback(
                        float(
                            upload_status.progress()
                        )
                    )
        except Exception as exc:
            message = (
                str(exc).strip()
                or str(
                    getattr(
                        exc,
                        "reason",
                        "",
                    )
                ).strip()
                or "erro desconhecido"
            )

            raise YouTubeError(
                f"O YouTube recusou o upload: {message}"
            ) from exc

        video_id = str(
            (response or {}).get(
                "id",
                "",
            )
        ).strip()

        if not video_id:
            raise YouTubeError(
                "O upload terminou sem retornar o ID do vÃ­deo."
            )

        returned_status = (
            (response or {})
            .get("status", {})
            .get(
                "privacyStatus",
                privacy_status,
            )
        )

        return {
            "video_id": video_id,
            "url": f"https://youtu.be/{video_id}",
            "title": metadata["title"],
            "privacy_status": returned_status,
            "scheduled_for": (
                publish_at
                or None
            ),
            "uploaded_at": _utc_now(),
        }


