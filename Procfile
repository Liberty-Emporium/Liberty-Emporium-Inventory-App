web: python -c "
import os
os.makedirs('/data/ads', exist_ok=True)
os.makedirs('/data/uploads', exist_ok=True)
os.makedirs('/data/backups', exist_ok=True)
os.makedirs('/data/music', exist_ok=True)
os.makedirs('/data/templates', exist_ok=True)
import shutil
import glob
# Seed templates if needed
if not os.path.exists('/data/templates/index.html'):
    src = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'templates')
    for f in glob.glob(os.path.join(src, '**', '*.html'), recursive=True):
        shutil.copy2(f, os.path.join('/data/templates', os.path.basename(f)))
" && exec gunicorn app_with_ai:app --bind 0.0.0.0:\$PORT --timeout 300 --workers 1 --worker-class gthread --threads 4 --capture-output --log-level info