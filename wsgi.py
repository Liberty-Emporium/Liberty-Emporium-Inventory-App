import sys
import os

# Add app directory to path (works locally and in containers)
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app_with_ai import app
application = app

if __name__ == '__main__':
    app.run(debug=False)
