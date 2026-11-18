#!/usr/bin/env python3
"""Voice Activity Radar.

Registra tudo que o usuário dita (atalho Deepgram), classifica à noite em
ATIVIDADES (não em comandos técnicos) e mantém um ranking do que ainda não
foi automatizado. Só biblioteca padrão: roda igual no Windows e na VPS.

Comandos:
  log       registra uma fala (usado pelo ditado)
  sync      (Windows/Mac) envia eventos para o servidor
  extract   classifica um dia (ou N dias) com o Claude e grava no banco
  export    gera export/activities.json (para painéis/dashboards)
  nightly   sync + extract + export
  report    ranking no terminal
  mark      marca status de uma atividade (pending|automating|automated|ignore)
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

HOME = Path(os.environ.get("VOICE_RADAR_HOME", Path.home() / ".voice-radar"))
EVENTS = HOME / "events"
EXPORT = HOME / "export"
WORK = HOME / "work"
DB_PATH = HOME / "radar.db"
HOST = os.environ.get("VOICE_RADAR_HOST") or ("windows" if os.name == "nt" else "mac" if sys.platform == "darwin" else "vps")
MODEL = os.environ.get("VOICE_RADAR_MODEL", "haiku")
VPS_ALIAS = os.environ.get("VOICE_RADAR_VPS", "")  # alias SSH do servidor; vazio = sem sync
VPS_HOME = os.environ.get("VOICE_RADAR_VPS_HOME", ".voice-radar")
STATUSES = ("pending", "automating", "automated", "ignore")

SYSTEM_PROMPT = (
    "Você classifica falas ditadas por um profissional/empresário em ATIVIDADES de trabalho. "
    "Responda SOMENTE com JSON válido, sem texto fora do JSON."
)

PROMPT = """Abaixo estão as falas que o usuário ditou no dia {date} para seus agentes de IA.

REGRAS
- Identifique as ATIVIDADES que ele pediu (o que ele quer ver feito no negócio), não os comandos técnicos.
  Exemplo ruim: "leia o arquivo", "edite o script", "rode o teste". Exemplo bom: "Publicar carrossel de cliente",
  "Editar vídeo de cliente", "Enviar mensagem no WhatsApp", "Subir deploy de site", "Criar proposta comercial".
- Nome da atividade: verbo no infinitivo + objeto, em português, curto (2 a 6 palavras), genérico o bastante para
  agrupar pedidos parecidos (não inclua nome de cliente no nome; o cliente vai em "clients").
- REUTILIZE exatamente o nome de uma atividade já conhecida quando a fala for do mesmo tipo.
- "count" = quantas falas do dia pediram essa atividade. Uma fala pode gerar mais de uma atividade.
- Ignore micro-comandos de código/arquivo ("leia o arquivo", "edite a porta", "rode o teste") quando não houver um
  objetivo de negócio claro por trás; o que importa é a atividade que o usuário repete no dia a dia.
- Ignore testes do próprio ditado ("teste, alô"), conversas sem pedido e qualquer segredo/credencial citado.
- "category": uma entre conteudo, video, sites, trafego, atendimento, financeiro, infra, vault, gestao, outros.
- "examples": até 2 paráfrases curtas (sem dados sensíveis).

ATIVIDADES JÁ CONHECIDAS:
{known}

FALAS (formato: [hora] (app) texto):
{lines}

Formato de resposta:
{{"activities":[{{"name":"...","category":"...","count":1,"clients":["..."],"examples":["..."]}}]}}
"""


def now():
    return dt.datetime.now().astimezone()


def init_dirs():
    for d in (HOME, EVENTS, EXPORT, WORK):
        d.mkdir(parents=True, exist_ok=True)


def db():
    init_dirs()
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS activities(
            id INTEGER PRIMARY KEY,
            name TEXT UNIQUE NOT NULL,
            category TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            first_seen TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS occurrences(
            date TEXT NOT NULL,
            activity_id INTEGER NOT NULL REFERENCES activities(id),
            count INTEGER NOT NULL,
            clients TEXT,
            examples TEXT,
            PRIMARY KEY(date, activity_id)
        );
        CREATE TABLE IF NOT EXISTS runs(
            date TEXT PRIMARY KEY,
            input_hash TEXT,
            events INTEGER,
            ran_at TEXT,
            model TEXT
        );
        """
    )
    return con


# ---------- registro ----------

def log_event(text, app="", title="", ts=None):
    """Acrescenta uma fala ao arquivo do dia. Nunca levanta exceção."""
    try:
        text = (text or "").strip()
        if not text:
            return
        t = ts or now()
        d = EVENTS / HOST
        d.mkdir(parents=True, exist_ok=True)
        rec = {"ts": t.isoformat(timespec="seconds"), "host": HOST, "app": app, "title": (title or "")[:120], "text": text}
        with open(d / f"{t.date().isoformat()}.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_events(date):
    out = []
    if not EVENTS.exists():
        return out
    for host_dir in sorted(p for p in EVENTS.iterdir() if p.is_dir()):
        f = host_dir / f"{date}.jsonl"
        if f.exists():
            for line in f.read_text(encoding="utf-8", errors="replace").splitlines():
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    out.sort(key=lambda r: r.get("ts", ""))
    return out


# ---------- sync (Windows/Mac -> servidor) ----------

def sync():
    if not VPS_ALIAS or HOST not in ("windows", "mac") or os.environ.get("VOICE_RADAR_HOME"):
        return 0
    src = EVENTS / HOST
    if not src.exists():
        return 0
    recent = sorted(src.glob("*.jsonl"))[-4:]
    if not recent:
        return 0
    base = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ClearAllForwardings=yes"]
    try:
        subprocess.run(["ssh", *base, VPS_ALIAS, f"mkdir -p {VPS_HOME}/events/{HOST}"],
                       check=True, capture_output=True, timeout=30)
        subprocess.run(["scp", "-q", *base, *[str(p) for p in recent], f"{VPS_ALIAS}:{VPS_HOME}/events/{HOST}/"],
                       check=True, capture_output=True, timeout=60)
        return len(recent)
    except Exception as e:
        print(f"sync falhou: {e}", file=sys.stderr)
        return -1


# ---------- classificação ----------

def call_llm(prompt):
    claude = shutil.which("claude") or "claude"
    WORK.mkdir(parents=True, exist_ok=True)
    cmd = [claude, "-p", "--model", MODEL, "--tools", "", "--setting-sources", "project",
           "--no-session-persistence", "--disable-slash-commands", "--strict-mcp-config",
           "--system-prompt", SYSTEM_PROMPT]
    r = subprocess.run(cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
                       timeout=420, cwd=str(WORK))
    if r.returncode != 0:
        raise RuntimeError(f"claude -p falhou ({r.returncode}): {r.stderr[:300]}")
    return r.stdout


def parse_json(raw):
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw)
    a, b = raw.find("{"), raw.rfind("}")
    if a < 0 or b < a:
        raise ValueError("resposta sem JSON")
    return json.loads(raw[a:b + 1])


def extract(date, force=False):
    events = load_events(date)
    con = db()
    if not events:
        return f"{date}: sem falas"
    h = hashlib.sha256("\n".join(e["ts"] + e["text"] for e in events).encode()).hexdigest()
    row = con.execute("SELECT input_hash FROM runs WHERE date=?", (date,)).fetchone()
    if row and row["input_hash"] == h and not force:
        return f"{date}: sem mudanças ({len(events)} falas)"

    known = [r["name"] for r in con.execute(
        "SELECT a.name FROM activities a LEFT JOIN occurrences o ON o.activity_id=a.id "
        "WHERE a.status!='ignore' GROUP BY a.id ORDER BY COALESCE(SUM(o.count),0) DESC LIMIT 80")]
    lines = "\n".join(
        f"[{e['ts'][11:16]}] ({e.get('app') or '?'}) {e['text'][:600]}" for e in events)[:60000]
    prompt = PROMPT.format(date=date, known="\n".join(f"- {k}" for k in known) or "(nenhuma ainda)", lines=lines)
    data = parse_json(call_llm(prompt))

    acts = data.get("activities", [])
    con.execute("DELETE FROM occurrences WHERE date=?", (date,))
    for a in acts:
        name = re.sub(r"\s+", " ", str(a.get("name", "")).strip())
        if not name:
            continue
        cat = str(a.get("category", "outros")).lower()
        con.execute("INSERT OR IGNORE INTO activities(name,category,first_seen) VALUES(?,?,?)", (name, cat, date))
        aid = con.execute("SELECT id FROM activities WHERE name=?", (name,)).fetchone()["id"]
        cnt = int(a.get("count") or 1)
        prev = con.execute("SELECT count FROM occurrences WHERE date=? AND activity_id=?", (date, aid)).fetchone()
        if prev:
            cnt += prev["count"]
        con.execute(
            "INSERT OR REPLACE INTO occurrences(date,activity_id,count,clients,examples) VALUES(?,?,?,?,?)",
            (date, aid, cnt, json.dumps(a.get("clients", []), ensure_ascii=False),
             json.dumps(a.get("examples", []), ensure_ascii=False)))
    con.execute("INSERT OR REPLACE INTO runs VALUES(?,?,?,?,?)", (date, h, len(events), now().isoformat(timespec="seconds"), MODEL))
    con.commit()
    return f"{date}: {len(events)} falas -> {len(acts)} atividades"


# ---------- export / relatório ----------

def week_key(d):
    y, w, _ = dt.date.fromisoformat(d).isocalendar()
    return f"{y}-W{w:02d}"


def build_export():
    con = db()
    today = now().date()
    rows = con.execute(
        "SELECT o.date,o.count,o.clients,o.examples,a.name,a.category,a.status FROM occurrences o "
        "JOIN activities a ON a.id=o.activity_id ORDER BY o.date").fetchall()
    daily, weekly = {}, {}
    totals = {}
    for r in rows:
        daily.setdefault(r["date"], []).append({"name": r["name"], "category": r["category"], "count": r["count"]})
        wk = weekly.setdefault(week_key(r["date"]), {})
        wk[r["name"]] = wk.get(r["name"], 0) + r["count"]
        t = totals.setdefault(r["name"], {"name": r["name"], "category": r["category"], "status": r["status"],
                                          "total": 0, "total_30d": 0, "days": set(), "last_seen": r["date"], "clients": set()})
        t["total"] += r["count"]
        if (today - dt.date.fromisoformat(r["date"])).days < 30:
            t["total_30d"] += r["count"]
        t["days"].add(r["date"])
        t["last_seen"] = max(t["last_seen"], r["date"])
        try:
            t["clients"].update(json.loads(r["clients"] or "[]"))
        except Exception:
            pass
    ranking = []
    for t in totals.values():
        pending = t["status"] in ("pending", "automating")
        ranking.append({
            "name": t["name"], "category": t["category"], "status": t["status"],
            "total": t["total"], "total_30d": t["total_30d"], "days_seen": len(t["days"]),
            "last_seen": t["last_seen"], "clients": sorted(t["clients"])[:8],
            # prioridade: repetição nos últimos 30 dias x quantos dias distintos; só para o que ainda não foi automatizado
            "priority": (t["total_30d"] * max(1, len(t["days"]))) if pending else 0,
        })
    ranking.sort(key=lambda r: (-r["priority"], -r["total"]))
    return {
        "generated_at": now().isoformat(timespec="seconds"),
        "host": HOST,
        "daily": [{"date": d, "total": sum(a["count"] for a in v), "activities": v} for d, v in sorted(daily.items())],
        "weekly": [{"week": w, "total": sum(v.values()),
                    "activities": [{"name": n, "count": c} for n, c in sorted(v.items(), key=lambda x: -x[1])]}
                   for w, v in sorted(weekly.items())],
        "ranking": ranking,
    }


def export():
    init_dirs()
    data = build_export()
    out = EXPORT / "activities.json"
    tmp = out.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(out)
    return out


def report():
    data = build_export()
    print(f"{'PRIO':>5} {'30d':>4} {'DIAS':>4}  {'STATUS':<10} ATIVIDADE")
    for r in data["ranking"][:30]:
        print(f"{r['priority']:>5} {r['total_30d']:>4} {r['days_seen']:>4}  {r['status']:<10} {r['name']} [{r['category']}]")
    if data["weekly"]:
        w = data["weekly"][-1]
        print(f"\nSemana {w['week']}: {w['total']} pedidos")


def mark(name, status):
    if status not in STATUSES:
        sys.exit(f"status deve ser um de {STATUSES}")
    con = db()
    cur = con.execute("UPDATE activities SET status=? WHERE lower(name)=lower(?)", (status, name))
    con.commit()
    print("ok" if cur.rowcount else "atividade não encontrada")


def nightly(days=2):
    print("sync:", sync())
    today = now().date()
    for i in range(days - 1, -1, -1):
        d = (today - dt.timedelta(days=i)).isoformat()
        try:
            print(extract(d))
        except Exception as e:
            print(f"{d}: erro {e}", file=sys.stderr)
    print("export:", export())


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("log"); s.add_argument("--text", required=True); s.add_argument("--app", default=""); s.add_argument("--title", default="")
    sub.add_parser("sync")
    s = sub.add_parser("extract"); s.add_argument("--date"); s.add_argument("--days", type=int, default=1); s.add_argument("--force", action="store_true")
    sub.add_parser("export")
    s = sub.add_parser("nightly"); s.add_argument("--days", type=int, default=2)
    sub.add_parser("report")
    s = sub.add_parser("mark"); s.add_argument("name"); s.add_argument("status")
    a = p.parse_args()
    if a.cmd == "log":
        log_event(a.text, a.app, a.title)
    elif a.cmd == "sync":
        print(sync())
    elif a.cmd == "extract":
        if a.date:
            print(extract(a.date, a.force))
        else:
            for i in range(a.days - 1, -1, -1):
                print(extract((now().date() - dt.timedelta(days=i)).isoformat(), a.force))
    elif a.cmd == "export":
        print(export())
    elif a.cmd == "nightly":
        nightly(a.days)
    elif a.cmd == "report":
        report()
    elif a.cmd == "mark":
        mark(a.name, a.status)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
