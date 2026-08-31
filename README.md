# CutLab AI

Aplicação local para transformar vídeos longos em cortes verticais prontos para YouTube Shorts, Instagram Reels e TikTok.

## O que o CutLab faz

- Baixa ou recebe vídeos longos.
- Transcreve o áudio e identifica os melhores momentos.
- Gera cortes verticais com reenquadramento e legendas.
- Permite revisar os cortes pela interface web local.
- Prepara metadados e publicação no YouTube.

## APIs e modelos aceitos

O CutLab permite escolher o provedor na interface. Os modelos disponíveis podem mudar conforme o provedor e a conta; use os nomes abaixo como referência ou informe outro modelo compatível no `.env`.

| Provedor | Variável de chave | Modelos configurados |
|---|---|---|
| Groq | `GROQ_API_KEY` | `qwen/qwen3.6-27b`, `openai/gpt-oss-20b`, `openai/gpt-oss-120b` |
| NVIDIA NIM | `NVIDIA_API_KEY` | `nvidia/nemotron-3.5-lightning-30b-a3b`, `nvidia/llama-3.3-nemotron-super-49b-v1.5`, `nvidia/nemotron-3-super-120b-a12b` |
| OpenAI | `OPENAI_API_KEY` | `gpt-4o-mini` (ou outro modelo compatível) |
| Google Gemini | `GEMINI_API_KEY` | `gemini-3.6-flash` (ou outro modelo disponível na sua conta) |
| LM Studio | não exige chave | modelos carregados localmente e expostos pela API compatível com OpenAI |

Para Groq, você pode definir o modelo no `.env`:

```env
GROQ_API_KEY=sua_chave_aqui
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=qwen/qwen3.6-27b
```

Para NVIDIA NIM:

```env
NVIDIA_API_KEY=sua_chave_aqui
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=nvidia/nemotron-3.5-lightning-30b-a3b
```

Para OpenAI:

```env
OPENAI_API_KEY=sua_chave_aqui
OPENAI_MODEL=gpt-4o-mini
```

Para Google Gemini:

```env
GEMINI_API_KEY=sua_chave_aqui
GEMINI_MODEL=gemini-3.6-flash
```

Para LM Studio, inicie o servidor local compatível com OpenAI e informe o modelo carregado:

```env
LMSTUDIO_BASE_URL=http://localhost:1234/v1
LMSTUDIO_MODEL=nome-do-modelo-carregado
```

Para MuAPI:

```env
MUAPI_API_KEY=sua_chave_aqui
MUAPI_BASE_URL=https://api.muapi.ai/api/v1
```

Use apenas um provedor LLM por vez na interface. O nome do modelo pode ser alterado conforme os modelos disponíveis na sua conta.

## Tutorial completo para Windows

### 1. Pré-requisitos

Instale [Python 3.10+](https://www.python.org/downloads/) e FFmpeg. Durante a instalação do Python, marque **Add Python to PATH**. O FFmpeg precisa estar disponível no PATH.

Confirme no PowerShell:

```powershell
python --version
ffmpeg -version
```

### 2. Baixe o projeto

```powershell
git clone https://github.com/lesadinhoCG/CutLab-AI.git
cd CutLab-AI
```

### 3. Instale as dependências

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

Se o PowerShell bloquear a ativação:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

### 4. Configure a IA

Crie `.env` na raiz do projeto. Nunca publique esse arquivo no GitHub.

Exemplo com Groq:

```env
GROQ_API_KEY=sua_chave_aqui
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_MODEL=llama-3.3-70b-versatile
```

Também são aceitas `OPENAI_API_KEY`, `GEMINI_API_KEY` ou um servidor local compatível com LM Studio.

### 5. Inicie o CutLab

```powershell
python server.py
```

Abra no navegador o endereço informado no terminal. No Windows, também é possível executar `abrir_cutlab.bat`.

### 6. Gere seu primeiro corte

1. Cole a URL do vídeo ou selecione um arquivo local.
2. Escolha o provedor/modelo de IA.
3. Defina formato `9:16`, duração, legendas e reenquadramento.
4. Inicie o processamento.
5. Revise os cortes e abra a pasta de saída.
6. Configure o YouTube na interface se quiser publicar diretamente.

## Solução de problemas

- **`python` não encontrado:** reinstale o Python com **Add Python to PATH**.
- **`ffmpeg` não encontrado:** adicione a pasta `bin` do FFmpeg ao PATH e abra um novo terminal.
- **Chave não configurada:** confira o `.env` e reinicie o servidor.
- **Dependência faltando:** ative `.venv` e execute novamente `python -m pip install -r requirements.txt`.

## Testes

```powershell
python -m pytest -q
python -m compileall -q app.py server.py main.py shorts_generator tests
```

## Licença

Distribuído sob a [MIT License](LICENSE).
