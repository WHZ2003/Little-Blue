"""One isolated Hermes process per application turn; no provider credentials here."""
import contextlib
import json
import os
from pathlib import Path
import sys


def refresh_persona(home, persona):
    """The application's core card owns identity; preserve the first old card."""
    soul = home / 'SOUL.md'
    old = soul.read_text(encoding='utf-8') if soul.exists() else None
    if old == persona:
        return
    previous = home / 'SOUL.before-app-refresh.md'
    if old is not None and not previous.exists():
        previous.write_text(old, encoding='utf-8')
    candidate = home / '.SOUL.app.tmp'
    candidate.write_text(persona, encoding='utf-8')
    candidate.replace(soul)


def main(data):
    import httpx
    from run_agent import AIAgent
    from tools.registry import registry
    from hermes_state import SessionDB

    headers = {'Authorization': 'Bearer ' + data['run_token']}
    proxy = os.environ.get('DAFEIYU_PROXY_URL', 'http://core:8765/harness')

    def handler(name):
        def call(args, **kwargs):
            with httpx.Client(trust_env=False, timeout=160) as client:
                response = client.post(proxy + '/tool', headers=headers, json={'name': name, 'arguments': args})
                response.raise_for_status()
                return json.dumps(response.json(), ensure_ascii=False)
        return call

    bridge = data.get('tools', [])
    for tool in bridge:
        schema = tool['function']
        registry.register(name=schema['name'], toolset='dafeiyu', schema=schema,
                          handler=handler(schema['name']), description=schema['description'])
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    refresh_persona(home, data.get('persona', data['system']))
    db = SessionDB(home / 'state.db')
    # Load the current app identity instead of Hermes' generic assistant identity.
    agent = AIAgent(model=data['model'], provider='custom', api_mode='chat_completions',
        api_key=data['run_token'], base_url=proxy + '/v1',
        enabled_toolsets=(['memory', 'skills', 'session_search'] if data.get('private_memory', True) else []) + (['dafeiyu'] if bridge else []),
        max_iterations=5, max_tokens=768, run_budget_seconds=210,
        quiet_mode=True, verbose_logging=False, save_trajectories=False,
        skip_context_files=True, load_soul_identity=True, skip_background_review=True,
        session_id='dafeiyu-' + data['task_id'], session_db=db, platform='dafeiyu',
        cwd=str(home / 'workspace'))
    try:
        result = agent.run_conversation(data['text'], system_message=data['system'],
                                        conversation_history=data['history'], task_id=data['task_id'])
        return {'ok': bool(result.get('completed')) and not result.get('failed'),
                'content': result.get('final_response', ''), 'api_calls': result.get('api_calls', 0),
                'harness': 'hermes'}
    finally:
        agent.close()
        db.close()


if __name__ == '__main__':
    data = json.loads(sys.stdin.buffer.read())
    home = Path(os.environ['HERMES_HOME'])
    home.mkdir(parents=True, exist_ok=True)
    (home / 'workspace').mkdir(exist_ok=True)
    config = home / 'config.yaml'
    if not config.exists():
        config.write_text('model:\n  provider: custom\n  context_length: 131072\n  streaming: false\n'
                          'tools:\n  tool_search:\n    enabled: off\n'
                          'compression:\n  enabled: false\n'
                          'auxiliary:\n  background_review:\n    enabled: false\n'
                          '  title_generation:\n    enabled: false\n', encoding='utf-8')
    # Upstream banners and logs must never mingle with the JSON transport.
    with contextlib.redirect_stdout(sys.stderr):
        result = main(data)
    print(json.dumps(result, ensure_ascii=False))
