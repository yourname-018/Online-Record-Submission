import os
import json
from datetime import datetime
from functools import wraps
from pathlib import Path
from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file, make_response
from werkzeug.utils import secure_filename

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'uploads'

# Allowed file extensions
ALLOWED_EXTENSIONS = {'pdf'}

# ========================
# UTILITY FUNCTIONS
# ========================

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def load_json(filename):
    """Load JSON data file from data/ directory"""
    path = Path('data') / filename
    if path.exists():
        # Handle empty files (0 bytes) or invalid JSON gracefully
        if path.stat().st_size == 0:
            return []
        try:
            with open(path, 'r') as f:
                return json.load(f)
        except json.JSONDecodeError:
            return []
    return []

def save_json(filename, data):
    """Save JSON data to data/ directory"""
    Path('data').mkdir(exist_ok=True)
    with open(Path('data') / filename, 'w') as f:
        json.dump(data, f, indent=2)

def login_required(f):
    """Decorator to check if user is logged in"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or 'role' not in session:
            return redirect(url_for('login_page'))
        return f(*args, **kwargs)
    return decorated_function

def role_required(role):
    """Decorator to check user role"""
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if session.get('role') != role:
                return redirect(url_for('login_page'))
            return f(*args, **kwargs)
        return decorated_function
    return decorator

# ========================
# ROUTES: AUTHENTICATION
# ========================

@app.route('/')
def index():
    """Redirect to login page"""
    if 'user_id' in session and 'role' in session:
        if session['role'] == 'student':
            return redirect(url_for('student_dashboard'))
        else:
            return redirect(url_for('faculty_dashboard'))
    return redirect(url_for('login_page'))

@app.route('/login', methods=['GET', 'POST'])
def login_page():
    """Combined login page - handles both GET and POST"""
    if request.method == 'POST':
        data = request.get_json() if request.is_json else request.form
        role = data.get('role', '').strip().lower()
        user_id = data.get('user_id', '').strip()
        password = data.get('password', '').strip()

        # Validate inputs
        if not role or not user_id or not password:
            return jsonify({'error': 'All fields are required'}), 400

        if role not in ['student', 'faculty']:
            return jsonify({'error': 'Invalid role'}), 400

        # Load credentials
        if role == 'student':
            users = load_json('students.json')
            id_field = 'studentID'
        else:  # faculty
            users = load_json('faculties.json')
            id_field = 'facultyID'

        # Authenticate
        user = next((u for u in users if u.get(id_field) == user_id and u.get('password') == password), None)

        if user:
            session['user_id'] = user.get(id_field)
            session['role'] = role
            session['name'] = user.get('name')
            if role == 'student':
                session['section'] = user.get('section', '')
            
            if role == 'student':
                return jsonify({'redirect': url_for('student_dashboard')}), 200
            else:
                return jsonify({'redirect': url_for('faculty_dashboard')}), 200
        else:
            return jsonify({'error': 'Invalid credentials'}), 401

    response = make_response(render_template('login.html'))
    # Prevent caching of login page
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/logout')
def logout():
    """Logout user"""
    session.clear()
    response = redirect(url_for('login_page'))
    # Prevent caching of logout response
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/user/current', methods=['GET'])
@login_required
def get_current_user():
    """Get current logged-in user info"""
    return jsonify({
        'user_id': session.get('user_id'),
        'name': session.get('name'),
        'role': session.get('role')
    })

# ========================
# ROUTES: STUDENT
# ========================

@app.route('/student/dashboard')
@login_required
@role_required('student')
def student_dashboard():
    """Student dashboard - view assignments and submissions"""
    response = make_response(render_template('student-dashboard.html'))
    # Prevent caching of dashboard
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/student/assignments', methods=['GET'])
@login_required
@role_required('student')
def get_student_assignments():
    """Get assignments visible to current student"""
    student_id = session['user_id']
    student_section = session.get('section', '')
    
    assignments = load_json('assignments.json')
    visible = []

    for a in assignments:
        target_type = a.get('targetType', 'all')
        
        # Check visibility
        if target_type == 'all':
            visible.append(a)
        elif target_type == 'section' and a.get('targetSection') == student_section:
            visible.append(a)
        elif target_type == 'students' and student_id in a.get('targetStudents', []):
            visible.append(a)

    return jsonify(visible)

@app.route('/api/student/submissions', methods=['GET'])
@login_required
@role_required('student')
def get_student_submissions():
    """Get submissions by current student"""
    student_id = session['user_id']
    submissions = load_json('submissions.json')
    
    student_subs = [s for s in submissions if s.get('studentID') == student_id]
    return jsonify(student_subs)

@app.route('/api/student/upload', methods=['POST'])
@login_required
@role_required('student')
def upload_submission():
    """Upload PDF submission"""
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400

    file = request.files['file']
    subject = request.form.get('subject', '').strip()

    if not file or not subject:
        return jsonify({'error': 'Missing file or subject'}), 400

    if not allowed_file(file.filename):
        return jsonify({'error': 'Only PDF files allowed'}), 400

    student_id = session['user_id']
    student_name = session['name']
    student_section = session.get('section', '')

    # Create upload folder
    upload_dir = Path(app.config['UPLOAD_FOLDER']) / student_id
    upload_dir.mkdir(parents=True, exist_ok=True)

    # Secure filename and save
    filename = secure_filename(file.filename)
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S_')
    filename = timestamp + filename
    filepath = upload_dir / filename

    file.save(str(filepath))

    # Record submission
    submissions = load_json('submissions.json')
    submission = {
        'id': len(submissions) + 1,
        'studentID': student_id,
        'name': student_name,
        'section': student_section,
        'subject': subject,
        'fileName': filename,
        'filePath': str(filepath),
        'submittedOn': datetime.now().isoformat(),
        'status': 'Pending',
        'remarks': ''
    }
    submissions.append(submission)
    save_json('submissions.json', submissions)

    return jsonify({'message': f'File uploaded for {subject}', 'submission': submission}), 200

@app.route('/api/student/submission/<int:sub_id>', methods=['DELETE'])
@login_required
@role_required('student')
def delete_submission(sub_id):
    """Delete own submission"""
    student_id = session['user_id']
    submissions = load_json('submissions.json')
    
    sub = next((s for s in submissions if s['id'] == sub_id and s['studentID'] == student_id), None)
    if not sub:
        return jsonify({'error': 'Submission not found'}), 404

    # Delete file if exists
    if 'filePath' in sub and Path(sub['filePath']).exists():
        Path(sub['filePath']).unlink()

    submissions = [s for s in submissions if s['id'] != sub_id]
    save_json('submissions.json', submissions)

    return jsonify({'message': 'Submission deleted'}), 200

# ========================
# ROUTES: FACULTY
# ========================

@app.route('/faculty/dashboard')
@login_required
@role_required('faculty')
def faculty_dashboard():
    """Faculty dashboard - manage assignments"""
    response = make_response(render_template('faculty-dashboard.html'))
    # Prevent caching of dashboard
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/faculty/submissions/<int:assign_id>')
@login_required
@role_required('faculty')
def submissions_view(assign_id):
    """View submissions for a specific assignment"""
    response = make_response(render_template('submitted-records.html', assign_id=assign_id))
    # Prevent caching of submissions page
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    return response

@app.route('/api/faculty/assignments', methods=['GET'])
@login_required
@role_required('faculty')
def get_faculty_assignments():
    """Get assignments created by current faculty"""
    faculty_id = session['user_id']
    assignments = load_json('assignments.json')
    
    faculty_assignments = [a for a in assignments if a.get('assignedBy') == faculty_id]
    return jsonify(faculty_assignments)

@app.route('/api/faculty/students', methods=['GET'])
@login_required
@role_required('faculty')
def get_all_students():
    """Get all students (for targeting assignments)"""
    students = load_json('students.json')
    # Remove passwords from response
    for s in students:
        s.pop('password', None)
    return jsonify(students)

@app.route('/api/faculty/create-assignment', methods=['POST'])
@login_required
@role_required('faculty')
def create_assignment():
    """Create new assignment"""
    data = request.get_json()
    
    # Validate
    subject = data.get('subject', '').strip()
    target_type = data.get('targetType', 'all')
    deadline_date = data.get('deadlineDate', '').strip()
    deadline_time = data.get('deadlineTime', '').strip()

    if not subject or not deadline_date or not deadline_time:
        return jsonify({'error': 'Missing required fields'}), 400

    faculty_id = session['user_id']
    faculty_name = session['name']

    # Load and create assignment
    assignments = load_json('assignments.json')
    assignment = {
        'id': len(assignments) + 1,
        'subject': subject,
        'targetType': target_type,
        'targetSection': data.get('targetSection'),
        'targetStudents': data.get('targetStudents', []),
        'deadlineDate': deadline_date,
        'deadlineTime': deadline_time,
        'assignedBy': faculty_id,
        'assignedByName': faculty_name,
        'createdAt': datetime.now().isoformat()
    }
    assignments.append(assignment)
    save_json('assignments.json', assignments)

    return jsonify({'message': 'Assignment created', 'assignment': assignment}), 200

@app.route('/api/faculty/assignment/<int:assign_id>', methods=['DELETE'])
@login_required
@role_required('faculty')
def delete_assignment(assign_id):
    """Delete assignment (only if created by current faculty)"""
    faculty_id = session['user_id']
    assignments = load_json('assignments.json')
    
    a = next((x for x in assignments if x['id'] == assign_id and x['assignedBy'] == faculty_id), None)
    if not a:
        return jsonify({'error': 'Assignment not found'}), 404

    assignments = [x for x in assignments if x['id'] != assign_id]
    save_json('assignments.json', assignments)

    return jsonify({'message': 'Assignment deleted'}), 200

@app.route('/api/faculty/submissions/<int:assign_id>', methods=['GET'])
@login_required
@role_required('faculty')
def get_assignment_submissions(assign_id):
    """Get submissions for a specific assignment"""
    faculty_id = session['user_id']
    assignments = load_json('assignments.json')
    
    assignment = next((a for a in assignments if a['id'] == assign_id and a['assignedBy'] == faculty_id), None)
    if not assignment:
        return jsonify({'error': 'Assignment not found'}), 404

    submissions = load_json('submissions.json')
    
    # Filter submissions for this assignment
    filtered = [s for s in submissions if s.get('subject') == assignment['subject']]

    # Further filter by target type
    if assignment['targetType'] == 'section':
        filtered = [s for s in filtered if s.get('section') == assignment['targetSection']]
    elif assignment['targetType'] == 'students':
        filtered = [s for s in filtered if s.get('studentID') in assignment.get('targetStudents', [])]

    return jsonify(filtered)

@app.route('/api/faculty/submission/<int:sub_id>/status', methods=['PUT'])
@login_required
@role_required('faculty')
def update_submission_status(sub_id):
    """Update submission status (Accept/Reject)"""
    data = request.get_json()
    new_status = data.get('status', '').strip()

    if new_status not in ['Accepted', 'Rejected']:
        return jsonify({'error': 'Invalid status'}), 400

    submissions = load_json('submissions.json')
    sub = next((s for s in submissions if s['id'] == sub_id), None)

    if not sub:
        return jsonify({'error': 'Submission not found'}), 404

    sub['status'] = new_status
    sub['remarks'] = data.get('remarks', '')
    save_json('submissions.json', submissions)

    return jsonify({'message': f'Submission marked as {new_status}'}), 200

@app.route('/api/faculty/download-submission/<int:sub_id>')
@login_required
@role_required('faculty')
def download_submission(sub_id):
    """Download submission file"""
    submissions = load_json('submissions.json')
    sub = next((s for s in submissions if s['id'] == sub_id), None)

    if not sub or 'filePath' not in sub:
        return jsonify({'error': 'File not found'}), 404

    filepath = Path(sub['filePath'])
    if not filepath.exists():
        return jsonify({'error': 'File not found on disk'}), 404

    return send_file(str(filepath), as_attachment=True, download_name=sub['fileName'])

@app.route('/api/faculty/submissions/<int:assign_id>/export')
@login_required
@role_required('faculty')
def export_submissions_csv(assign_id):
    """Export submissions as CSV"""
    import csv
    from io import StringIO

    faculty_id = session['user_id']
    assignments = load_json('assignments.json')
    
    assignment = next((a for a in assignments if a['id'] == assign_id and a['assignedBy'] == faculty_id), None)
    if not assignment:
        return jsonify({'error': 'Assignment not found'}), 404

    # Get filtered submissions (same logic as get_assignment_submissions)
    submissions = load_json('submissions.json')
    filtered = [s for s in submissions if s.get('subject') == assignment['subject']]

    if assignment['targetType'] == 'section':
        filtered = [s for s in filtered if s.get('section') == assignment['targetSection']]
    elif assignment['targetType'] == 'students':
        filtered = [s for s in filtered if s.get('studentID') in assignment.get('targetStudents', [])]

    # Create CSV
    output = StringIO()
    writer = csv.DictWriter(output, fieldnames=['Student Name', 'ID', 'Section', 'File', 'Submitted', 'Status'])
    writer.writeheader()

    for s in filtered:
        writer.writerow({
            'Student Name': s.get('name'),
            'ID': s.get('studentID'),
            'Section': s.get('section'),
            'File': s.get('fileName'),
            'Submitted': s.get('submittedOn', 'N/A'),
            'Status': s.get('status')
        })

    # Return as file
    output.seek(0)
    return app.response_class(
        output.getvalue(),
        mimetype='text/csv',
        headers={'Content-Disposition': f'attachment;filename={assignment["subject"]}_submissions.csv'}
    )

# ========================
# ERROR HANDLERS
# ========================

@app.errorhandler(404)
def not_found(e):
    return jsonify({'error': 'Not found'}), 404

@app.errorhandler(500)
def server_error(e):
    return jsonify({'error': 'Server error'}), 500

# ========================
# INITIALIZATION
# ========================

def init_app():
    """Initialize app structure"""
    Path('data').mkdir(exist_ok=True)
    Path(app.config['UPLOAD_FOLDER']).mkdir(exist_ok=True)
    
    # Create empty JSON files if not exist
    for filename in ['students.json', 'faculties.json', 'assignments.json', 'submissions.json']:
        path = Path('data') / filename
        if not path.exists():
            with open(path, 'w') as f:
                json.dump([], f)

if __name__ == '__main__':
    init_app()
    app.run(debug=True, host='0.0.0.0', port=5000)