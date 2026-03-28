import sys
import os

sys.path.insert(0, '/home/edclawd/liberty-emporium-demo')

from app_with_ai import app
application = app

if __name__ == '__main__':
    app.run()
