import urllib.request, os, zipfile, sys
base = r'C:\Users\j.canadas\Mark-XXXIX'
dest = os.path.join(base, 'tools', 'mpv')
os.makedirs(dest, exist_ok=True)
url = 'https://sourceforge.net/projects/mpv-player-windows/files/latest/download'
print('Downloading...', url)
try:
    with urllib.request.urlopen(url) as resp:
        data = resp.read()
except Exception as e:
    print('Download failed:', e)
    sys.exit(1)
zip_path = os.path.join(dest, 'mpv.zip')
with open(zip_path, 'wb') as f:
    f.write(data)
print('Extracting to', dest)
try:
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
except Exception as e:
    print('Extract failed:', e)
    sys.exit(1)
os.remove(zip_path)
print('DONE')
