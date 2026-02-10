import time
from notifier import notify

def run():
    print("trackerbundle çalışıyor")
    notify("🚀 Bot ayağa kalktı")
    while True:
        time.sleep(60)

