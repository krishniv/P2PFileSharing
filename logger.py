import sys
import threading
from datetime import datetime

class Logger:
    def __init__(self, filename):
        self.filename = filename
        self.lock = threading.Lock()
        
    def create_log(self, msg):
        time_str = datetime.now().ctime()
        log_line = f"[{time_str}]: {msg}\n"
        
        with self.lock:
            with open(self.filename, 'a') as f:
                f.write(log_line)
            sys.stdout.write(log_line)
            sys.stdout.flush()
