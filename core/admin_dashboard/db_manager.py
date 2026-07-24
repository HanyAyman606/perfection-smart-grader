import sqlite3
import json

def initialize_database():
    # Connect to SQLite (this creates the file if it doesn't exist)
    conn = sqlite3.connect("grading_system.db")
    cursor = conn.cursor()

    # 1. Students Table (For cross-verification)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            student_name TEXT NOT NULL
        )
    """)

    # 2. Sessions Table (The Session Bank)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            exam_name TEXT NOT NULL,
            exam_type TEXT NOT NULL, -- e.g., 'quiz' (3-digit) or 'shamel' (4-digit)
            has_essays BOOLEAN NOT NULL,
            essay_config TEXT, -- Stored as JSON: [{"q_num": 45, "max": 2}]
            answer_key TEXT NOT NULL, -- Stored as JSON: [{"q": 1, "ans": "B", "weight": 1}]
            roi_coordinates TEXT, -- Stored as JSON: {"x": 100, "y": 200, "w": 500, "h": 600}
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # 3. Grades Table (The final output)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS grades (
            scan_id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            student_id TEXT NOT NULL,
            mcq_score INTEGER NOT NULL,
            essay_total INTEGER DEFAULT 0,
            final_score INTEGER NOT NULL,
            mistakes_log TEXT, -- Stored as JSON to map exact errors
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (session_id) REFERENCES sessions (session_id),
            FOREIGN KEY (student_id) REFERENCES students (student_id),
            UNIQUE(session_id, student_id) -- This PREVENTS the exact same student from being graded twice in one session
        )
    """)

    conn.commit()
    conn.close()
    print("Database and tables initialized successfully!")

if __name__ == "__main__":
    initialize_database()