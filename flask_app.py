# PythonAnywhere WSGI entry point.
# In the Web tab -> WSGI configuration file, replace contents with something like:
#
#   import sys
#   path = '/home/yourname/egx_report'
#   if path not in sys.path:
#       sys.path.insert(0, path)
#   from flask_app import app as application
#
# Set env vars in the same WSGI file (os.environ[...] = "...") since
# PythonAnywhere free tier has no separate secrets manager.

from bot import app  # noqa: F401
