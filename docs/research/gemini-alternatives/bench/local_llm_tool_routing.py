"""Ask a local OpenAI-compatible server (LM Studio / Ollama) to route 8 Ukrainian commands
with the real actions/ tool declarations and core/prompt.txt. Usage: python local_llm_tool_routing.py MODEL [URL]"""
import sys, json, time, re, urllib.request
from pathlib import Path
ROOT = Path(__file__).resolve().parents[3]   # repo root
sys.path.insert(0, str(ROOT))
from core.action_loader import discover_actions
MODEL = sys.argv[1]; URL = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:1234/v1/chat/completions"
reg = discover_actions(ROOT / "actions", logger=lambda m: None)
def conv(s):
    if isinstance(s, dict):
        return {k: (v.lower() if k == "type" and isinstance(v, str) else conv(v)) for k, v in s.items()}
    if isinstance(s, list): return [conv(x) for x in s]
    return s
tools = [{"type": "function", "function": {"name": d["name"], "description": d["description"],
          "parameters": conv(d.get("parameters") or {"type": "object", "properties": {}})}}
         for d in reg.get_tool_declarations()]
system = (ROOT / "core" / "prompt.txt").read_text(encoding="utf-8")
system = re.sub(r"\{[a-z_]+\}", "", system)
cases = [("Відкрий блокнот", "open_app"),
         ("Зроби гучність тридцять відсотків", "computer_settings"),
         ("Напиши Андрію Коваленку в слаку: привіт, як справи?", "send_message"),
         ("Очисти кошик", "file_controller"),
         ("Увімкни нічне світло в налаштуваннях Windows", "computer_use"),
         ("Яка зараз погода в Києві?", "weather_report"),
         ("Відправ це повідомлення", "send_message"),
         ("Натисни кнопку Зберегти у вікні", "computer_control")]
ok = 0
for q, want in cases:
    body = {"model": MODEL, "messages": [{"role": "system", "content": system}, {"role": "user", "content": q}],
            "tools": tools, "temperature": 0.2, "max_tokens": 2000}
    s = time.monotonic()
    req = urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"})
    r = json.load(urllib.request.urlopen(req, timeout=180))
    dt = time.monotonic() - s
    m = r["choices"][0]["message"]; calls = m.get("tool_calls") or []
    got = calls[0]["function"]["name"] if calls else "-"
    args = calls[0]["function"]["arguments"][:110] if calls else (m.get("content") or "")[:110].replace("\n", " ")
    hit = got == want; ok += hit
    print(f"{'OK ' if hit else 'BAD'} {dt:5.1f}s  {q[:42]:42} -> {got} {args}")
u = r.get("usage", {}); print(f"{MODEL}: {ok}/{len(cases)} correct; prompt tokens {u.get('prompt_tokens')}")
