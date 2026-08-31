# CutLab AI

Aplicação local para transformar vídeos longos em cortes verticais prontos para YouTube Shorts, Instagram Reels e TikTok.

## O que o CutLab faz

- Baixa ou recebe vídeos longos.
- Transcreve o áudio e identifica os melhores momentos.
- Gera cortes verticais com reenquadramento e legendas.
- Permite revisar os cortes pela interface web local.
- Prepara metadados e publicação no YouTube.

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
