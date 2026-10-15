import os
from pathlib import Path
import requests
import psycopg2
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')


def pick(*names):
    for name in names:
        value = (os.getenv(name) or '').strip()
        if value:
            return name, value
    return None, None


def main():
    print('=' * 72)
    print('VIEWER MANAGER v0.4')
    print('POSTGRES_HOST =', os.getenv('POSTGRES_HOST'))
    print('POSTGRES_DB   =', os.getenv('POSTGRES_DB'))

    cid_name, cid = pick('APS_CLIENT_ID_S2S', 'APS_CLIENT_ID', 'ACC_CLIENT_ID')
    sec_name, sec = pick('APS_CLIENT_SECRET_S2S', 'APS_CLIENT_SECRET', 'ACC_CLIENT_SECRET')
    print('APS CLIENT ID =', cid_name or 'NO CONFIGURADO')
    print('APS SECRET    =', '***' if sec else 'NO CONFIGURADO')
    print('=' * 72)

    conn = psycopg2.connect(
        host=os.getenv('POSTGRES_HOST', 'localhost'),
        port=os.getenv('POSTGRES_PORT', '5432'),
        dbname=os.getenv('POSTGRES_DB', 'bpmbimgis'),
        user=os.getenv('POSTGRES_USER'),
        password=os.getenv('POSTGRES_PASSWORD'),
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT
                  to_regclass('public.bim_projects'),
                  to_regclass('public.bim_project_models'),
                  to_regclass('public.bim_remote_versions'),
                  to_regclass('public.bim_local_versions'),
                  to_regclass('public.v_bim_viewer_state')
            """)
            row = cur.fetchone()
        labels = ['bim_projects','bim_project_models','bim_remote_versions',
                  'bim_local_versions','v_bim_viewer_state']
        for label, value in zip(labels, row):
            print(f'{label:28}', 'OK' if value else 'FALTA')
    finally:
        conn.close()

    if not cid or not sec:
        print('APS viewables:read           FALTA CONFIGURAR S2S')
        raise SystemExit(2)

    r = requests.post(
        'https://developer.api.autodesk.com/authentication/v2/token',
        headers={'Content-Type': 'application/x-www-form-urlencoded'},
        data={
            'client_id': cid,
            'client_secret': sec,
            'grant_type': 'client_credentials',
            'scope': 'viewables:read',
        },
        timeout=30,
    )
    if r.ok and r.json().get('access_token'):
        print('APS viewables:read           OK')
    else:
        print('APS viewables:read           ERROR', r.status_code)
        print(r.text[:500])
        raise SystemExit(3)

    print('=' * 72)
    print('OK: Viewer Manager listo para prueba.')


if __name__ == '__main__':
    main()
