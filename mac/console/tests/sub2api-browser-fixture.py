"""In-memory Sub2API UI fixture; no real keys, visitor or upstream requests."""
import copy
import sys
import threading

from app.server import Handler, LocalHTTPServer


class Fixture:
    def __init__(self):
        self.value = {
            'version': 'Sub2API UI fixture',
            'ollama': {'running': True, 'managed': False, 'checked': False,
                       'ok': False, 'url': 'http://127.0.0.1:11435'},
            'model_routes': [], 'selected_model_route': '',
            'apps': {'ccswitch': True},
            'routes': [{'id': 'stcp', 'name': 'Windows Sub2API · 测试连接'},
                       {'id': 'external', 'name': '外部 visitor · 隔离示例'},
                       {'id': 'direct', 'name': '直连 · 隔离示例'}],
            'selected_route': 'stcp',
            'sub2api': {'configured': True, 'running': False, 'listener': False,
                        'ownership': 'stopped', 'mode': 'stcp',
                        'url': 'http://127.0.0.1:18081/v1', 'key_available': True,
                        'remote': {'state': 'unchecked', 'detail': 'fixture'}},
        }

    def status(self, passive=False):
        value = copy.deepcopy(self.value)
        value.update(passive=bool(passive), active_chats=0)
        return value

    def action(self, service, action):
        value = self.value['sub2api']
        if service == 'route':
            self.value['selected_route'] = action
            value.update(mode='direct' if action == 'direct' else 'stcp',
                         running=False, listener=action == 'external',
                         ownership='external' if action == 'external' else 'stopped')
            value['remote'] = {'state': 'unchecked'}
        elif service == 'sub2api' and action in ('start', 'stop'):
            running = action == 'start'
            value.update(running=running, listener=running,
                         ownership='owned' if running else 'stopped')
            value['remote'] = {'state': 'unchecked'}
        elif service == 'sub2api' and action == 'check':
            value['remote'] = {'state': 'ok', 'detail': '隔离示例，无真实请求', 'latency_ms': 1}
        return '隔离界面操作完成；未启动真实连接或应用。'


if __name__ == '__main__':
    server = LocalHTTPServer(('127.0.0.1', 0), Handler)
    server.authority = f'127.0.0.1:{server.server_port}'
    server.token = 'local-fixture-only'
    server.controller = Fixture()
    print('http://' + server.authority, flush=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        sys.stdin.read()
    finally:
        server.shutdown()
        server.server_close()
