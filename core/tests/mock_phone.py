import asyncio
import websockets
import json

async def simulate_scan():
    uri = "ws://localhost:8000/ws/grade"
    
    # This is the exact payload Eyad's OpenCV engine will eventually send
    fake_payload = {
        "action": "submit_grade",
        "session_id": "quiz_week_4",
        "student_id": "Z123",
        "mcq_score": 18,
        "essay_scores": [2, 3],
        "total_score": 23,
        "mistakes": [
            {"q": 5, "student_ans": "B", "correct_ans": "C"}
        ]
    }

    try:
        async with websockets.connect(uri) as websocket:
            print("Connected to the PySide Server!")
            
            # Send the payload
            print(f"Sending payload: {json.dumps(fake_payload, indent=2)}")
            await websocket.send(json.dumps(fake_payload))
            
            # Wait for the server's response
            response = await websocket.recv()
            print(f"\nReceived Server Response:\n{json.dumps(json.loads(response), indent=2)}")
            
    except ConnectionRefusedError:
        print("Error: Could not connect. Is the network_server.py running?")

if __name__ == "__main__":
    asyncio.run(simulate_scan())