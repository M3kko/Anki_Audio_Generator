import multiprocessing

# Worker timeout - set to 10 minutes for audio generation
timeout = 600

# Number of worker processes
workers = 1

# Worker class
worker_class = 'sync'

# Bind to Railway's port
bind = '0.0.0.0:8080'
