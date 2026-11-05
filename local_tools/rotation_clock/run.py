"""python local_tools/rotation_clock/run.py --port 8011 [--demo]"""
import argparse
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]


def main():
    parser = argparse.ArgumentParser(description='独立本地轮岗模拟器，不连接现有业务数据库')
    parser.add_argument('--data-dir', default=str(ROOT / 'tmp/rotation-simulator/data'))
    parser.add_argument('--port', type=int, default=8011)
    parser.add_argument('--demo', action='store_true', help='首次创建模拟账号、班表和预排；只允许空模拟器数据库')
    args = parser.parse_args()
    from local_tools.rotation_clock.guard import prepare, exclusive_data
    path = prepare(args.data_dir)
    if args.demo or (path / '.demo-ready').exists():
        os.environ['RECOGNITION_ENABLE_TEST_ACCOUNTS'] = '1'
        os.environ['RECOGNITION_TEST_DEFAULT_PASSWORD'] = 'rotation-local-2026'
        os.environ['RECOGNITION_TEST_ADMIN_PASSWORD'] = 'rotation-local-admin-2026'
    with exclusive_data(path):
        from app.main import app
        from local_tools.rotation_clock.extension import install
        install(app)
        if args.demo:
            from local_tools.rotation_clock.demo import seed
            seed(path)
        print(f'本地轮岗模拟器 http://127.0.0.1:{args.port}/rotation-board\n独立数据目录: {path}', flush=True)
        import uvicorn
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning')


if __name__ == '__main__':
    main()
