def main():
    emit('=== Local filesystem space ===')
    emit(run(['df', '-l', '-h', '-P'], truncate=True))
    emit('=== Local filesystem inodes ===')
    emit(run(['df', '-l', '-i', '-P'], truncate=True))
