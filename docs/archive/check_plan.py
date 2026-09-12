import sqlite3
import json

conn = sqlite3.connect('../data/eas.db')
cursor = conn.cursor()

plan_id = '28a10daa-51d0-44b6-a6eb-e4dbf7cfd649'

# Check steps table
cursor.execute('SELECT COUNT(*) FROM steps WHERE plan_id = ?', (plan_id,))
steps_count = cursor.fetchone()[0]
print(f'Steps in steps table: {steps_count}')

# Check segments JSON
cursor.execute('SELECT segments FROM plans WHERE plan_id = ?', (plan_id,))
row = cursor.fetchone()
if row:
    segments = json.loads(row[0])
    total_steps = sum(len(s.get('steps', [])) for s in segments)
    print(f'Steps in segments JSON: {total_steps}')
    print(f'\nSegments: {len(segments)}')
    for i, seg in enumerate(segments):
        print(f'  Segment {i+1}: {seg.get("description")} ({len(seg.get("steps", []))} steps)')
else:
    print('Plan not found')

conn.close()
