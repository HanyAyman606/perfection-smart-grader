import sqlite3
import json
from fastapi import FastAPI, WebSocket, WebSocketDisconnect

app = FastAPI()

# Connection manager to handle multiple mobile phones scanning at once
class ConnectionManager:
    def __init__(self):
        self.active_connections: list[WebSocket] = []

    async def connect(self, websocket: WebSocket):
        await websocket.accept()
        self.active_connections.append(websocket)

    def disconnect(self, websocket: WebSocket):
        self.active_connections.remove(websocket)

    async def send_response(self, message: dict, websocket: WebSocket):
        await websocket.send_json(message)

manager = ConnectionManager()

def process_scan_payload(session_id, student_id, mcq_score, essay_scores, total_score, mistakes):
    """Handles the database validation and insertion securely."""
    essay_total = sum(essay_scores) if essay_scores else 0
    
    try:
        conn = sqlite3.connect("grading_system.db")
        cursor = conn.cursor()
        
        # 1. Check if the Student ID exists in our registry
        cursor.execute("SELECT student_name FROM students WHERE student_id = ?", (student_id,))
        student = cursor.fetchone()
        
        if not student:
            return {
                "status": "error", 
                "error_type": "id_not_found", 
                "message": f"ID {student_id} not found in registry. Please verify manually."
            }
            
        # We fetch the name for the PySide Dashboard, but we DO NOT send it to Flutter
        student_name = student[0] 
        
        # 2. Attempt to save the grade (The UNIQUE constraint prevents duplicates)
        cursor.execute("""
            INSERT INTO grades (session_id, student_id, mcq_score, essay_total, final_score, mistakes_log)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (session_id, student_id, mcq_score, essay_total, total_score, json.dumps(mistakes)))
        
        conn.commit()
        
        # SUCCESS RESPONSE (Name Removed)
        return {
            "status": "success", 
            "total_score": total_score, 
            "message": "Grade saved successfully."
        }
        
    except sqlite3.IntegrityError:
        # 3. Catch the duplicate ID error instantly
        cursor.execute("SELECT final_score FROM grades WHERE session_id = ? AND student_id = ?", (session_id, student_id))
        prev_score = cursor.fetchone()[0]
        
        # DUPLICATE RESPONSE (Name Removed)
        return {
            "status": "error", 
            "error_type": "duplicate_id", 
            "previous_score": prev_score, 
            "message": "Duplicate Scan! This paper has already been graded."
        }
    finally:
        conn.close()

@app.websocket("/ws/grade")
async def grading_endpoint(websocket: WebSocket):
    """The WebSocket endpoint the Flutter app connects to."""
    await manager.connect(websocket)
    try:
        while True:
            # Wait for the phone to send the scanned data
            payload = await websocket.receive_json()
            
            # Extract the data
            session_id = payload.get("session_id")
            student_id = payload.get("student_id")
            mcq_score = payload.get("mcq_score")
            essay_scores = payload.get("essay_scores", [])
            total_score = payload.get("total_score")
            mistakes = payload.get("mistakes", [])
            
            # Process and validate via SQLite
            server_response = process_scan_payload(
                session_id, student_id, mcq_score, essay_scores, total_score, mistakes
            )
            
            # Immediately send the result back to the phone screen
            await manager.send_response(server_response, websocket)
            
    except WebSocketDisconnect:
        manager.disconnect(websocket)

if __name__ == "__main__":
    import uvicorn
    # Run the server locally on port 8000
    uvicorn.run(app, host="0.0.0.0", port=8000)