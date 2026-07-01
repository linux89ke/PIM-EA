import os
import time

def list_recent_files():
    files = []
    for root, dirs, filenames in os.walk('.'):
        for f in filenames:
            path = os.path.join(root, f)
            try:
                mtime = os.path.getmtime(path)
                files.append((path, mtime))
            except:
                pass
    files.sort(key=lambda x: x[1], reverse=True)
    print("Top 15 most recently modified files:")
    for path, mtime in files[:25]:
        print(f"{time.ctime(mtime)}: {path}")

if __name__ == "__main__":
    list_recent_files()
