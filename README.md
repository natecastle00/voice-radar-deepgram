# Voice Radar Deepgram

Ditado por voz no Windows (Deepgram) + um "radar" que descobre **quais atividades você mais repete**, para você saber o que automatizar primeiro.

```
Ctrl+Win (segura ou 2x)  →  Deepgram  →  texto digitado ao vivo onde o cursor está
                                  └──→  cada fala é registrada (radar log)
                                                └──→ à noite o radar classifica em ATIVIDADES
                                                     e gera ranking diário/semanal
```

## Componentes

| Pasta | O que faz |
|---|---|
| `dictation/` | App de bandeja do Windows: atalho global, gravação, transcrição em tempo real (WebSocket Deepgram `nova-3`, `pt-BR`), digitação ao vivo, HUD e histórico. |
| `radar/radar.py` | CLI só com biblioteca padrão: registra falas, classifica por dia com o Claude Code CLI, guarda em SQLite, ranqueia e exporta `activities.json`. |
| `deploy/` | Exemplos: tarefa agendada do Windows e `systemd` (service + timer) para rodar o radar numa VPS. |

## Requisitos

- Windows 10/11 e Python 3.10+
- Conta [Deepgram](https://deepgram.com) (chave de API)
- Para a classificação: [Claude Code CLI](https://claude.com/claude-code) (`claude`) instalado e autenticado

## Instalação do ditado

```powershell
cd dictation
pip install -r requirements.txt
copy .env.example .env          # edite e cole sua DEEPGRAM_API_KEY
copy config.example.json config.json   # opcional: ajuste atalho/modelo
python dictation.py
```

A chave é lida de `DEEPGRAM_API_KEY` (variável de ambiente), do `.env` ao lado do `dictation.py`, ou do arquivo apontado em `env_file_path` no `config.json`. **Nunca versione o `.env`.**

Iniciar sem janela: `start_silent.vbs`. Iniciar com o Windows: `install_startup.ps1` (remover: `remove_startup.ps1`). Encerrar: `stop.bat`.

### Uso

- **Segurar** `Ctrl+Win` enquanto fala; soltar encerra.
- **Toque rápido** em `Ctrl+Win` (ou `F8`) liga a gravação com mãos livres; outro toque encerra.
- O texto aparece digitado ao vivo (`live_typing`), fica no clipboard e no histórico (`history.txt`, clique direito no ícone da bandeja).

Principais opções do `config.json`: `hotkey`, `secondary_hotkey`, `model`, `language`, `live_typing`, `endpointing_ms`, `auto_paste`.

## Radar

O ditado chama `radar.log_event()` a cada fala. Os eventos ficam em `~/.voice-radar/events/<host>/<data>.jsonl` (campos: horário, app em foco, título da janela, texto). **Tudo fica na sua máquina**; só vai para fora se você configurar o sync ou usar a classificação (o texto das falas é enviado ao modelo do Claude Code).

```powershell
python radar\radar.py report                    # ranking no terminal
python radar\radar.py extract --date 2026-01-31 # classifica um dia
python radar\radar.py nightly                   # sync + extract + export
python radar\radar.py export                    # gera ~/.voice-radar/export/activities.json
python radar\radar.py mark "Publicar carrossel" automated   # pending|automating|automated|ignore
```

Variáveis de ambiente: `VOICE_RADAR_HOME` (pasta de dados), `VOICE_RADAR_MODEL` (padrão `haiku`), `VOICE_RADAR_HOST`.

### Rodar toda noite

- Windows: `deploy\windows-nightly.ps1` cria a tarefa agendada às 23:50.
- Servidor Linux (opcional): copie o repositório para `/opt/voice-radar`, instale `deploy/voice-radar.service` e `.timer` em `/etc/systemd/system/` e rode `systemctl enable --now voice-radar.timer`.
- Sync Windows → servidor: defina `VOICE_RADAR_VPS` com um alias SSH (e `VOICE_RADAR_VPS_HOME`, padrão `.voice-radar`). Sem isso, o sync fica desligado.

### Exportação

`activities.json` traz o ranking de atividades por repetição (total do dia, da semana, status). Dá para ler com qualquer dashboard.

## Privacidade

O radar registra o que você dita e o título da janela ativa. Revise `~/.voice-radar` antes de compartilhar qualquer dado, e não cole segredos falando em voz alta (a classificação ignora credenciais citadas, mas o texto bruto fica no log local).

## Licença

MIT

## Notificações no WhatsApp (opcional)

`.github/workflows/notify-whatsapp.yml` avisa um grupo de WhatsApp via [Evolution API](https://github.com/EvolutionAPI/evolution-api) a cada push na `main` e a cada release. Para usar no seu fork, crie os secrets `EVOLUTION_URL`, `EVOLUTION_API_KEY`, `EVOLUTION_INSTANCE` e `WHATSAPP_GROUP_JID`. Sem eles, o envio é pulado.
