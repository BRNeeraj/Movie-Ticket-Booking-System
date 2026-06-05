from flask import Flask, jsonify, render_template, request, redirect, session
import mysql.connector
from mysql.connector import IntegrityError
from werkzeug.utils import secure_filename
from werkzeug.security import check_password_hash, generate_password_hash
import os
import uuid

app = Flask(__name__)

app.secret_key = "movie_secret_key"

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "static", "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

ADMIN_EMAILS = {
    os.environ.get("ADMIN_EMAIL", "admin@gmail.com").lower(),
    "owner@gmail.com"
}
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin123")

# DATABASE CONNECTION

DB_CONFIG = {
    "host": "localhost",
    "user": "root",
    "password": "1234",
    "database": "movie_ticket_booking"
}

conn = mysql.connector.connect(**DB_CONFIG)

cursor = conn.cursor()

user_table_security_ready = False
booking_sql_objects_ready = False


def db_cursor(dictionary=False):

    global conn
    global cursor

    try:

        conn.ping(reconnect=True, attempts=3, delay=1)

    except mysql.connector.Error:

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

    if not conn.is_connected():

        conn = mysql.connector.connect(**DB_CONFIG)
        cursor = conn.cursor()

    return conn.cursor(dictionary=dictionary)


def ensure_admin_tables():

    setup_cursor = db_cursor()

    setup_cursor.execute("""
    CREATE TABLE IF NOT EXISTS AdminMovie (
        id INT AUTO_INCREMENT PRIMARY KEY,
        title VARCHAR(150) NOT NULL,
        genre VARCHAR(100),
        language VARCHAR(60),
        format_type VARCHAR(60),
        duration VARCHAR(60),
        cast VARCHAR(255),
        director VARCHAR(120),
        rating VARCHAR(20),
        description TEXT,
        poster VARCHAR(255),
        trailer VARCHAR(255),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    setup_cursor.execute("""
    CREATE TABLE IF NOT EXISTS SeatInventory (
        id INT AUTO_INCREMENT PRIMARY KEY,
        movie_id VARCHAR(40) NOT NULL,
        theatre VARCHAR(80) NOT NULL,
        show_date VARCHAR(30) NOT NULL,
        show_time VARCHAR(30) NOT NULL,
        seat_number VARCHAR(20) NOT NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'blocked',
        booking_ref VARCHAR(60),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE KEY unique_show_seat (
            movie_id,
            theatre,
            show_date,
            show_time,
            seat_number
        )
    )
    """)

    conn.commit()
    setup_cursor.close()


def ensure_user_table_security():

    global user_table_security_ready

    if user_table_security_ready:

        return

    user_cursor = db_cursor()

    try:

        user_cursor.execute(
            "ALTER TABLE users MODIFY password VARCHAR(255)"
        )
        conn.commit()

    except mysql.connector.Error:

        conn.rollback()

    for index_name, column_name in (
        ("unique_users_username", "username"),
        ("unique_users_email", "email")
    ):

        try:

            user_cursor.execute(
                f"CREATE UNIQUE INDEX {index_name} ON users({column_name})"
            )
            conn.commit()

        except mysql.connector.Error:

            conn.rollback()

    user_cursor.close()
    user_table_security_ready = True


def ensure_booking_sql_objects():

    global booking_sql_objects_ready

    if booking_sql_objects_ready:

        return

    booking_cursor = db_cursor()

    try:

        booking_cursor.execute("DROP TRIGGER IF EXISTS prevent_duplicate_seat")
        booking_cursor.execute("""
        CREATE TRIGGER prevent_duplicate_seat
        BEFORE INSERT ON Booking
        FOR EACH ROW
        BEGIN

            IF EXISTS (
                SELECT 1
                FROM Booking
                WHERE movie_id = NEW.movie_id
                AND seat_number = NEW.seat_number
            ) THEN

                SIGNAL SQLSTATE '45000'
                SET MESSAGE_TEXT = 'Seat already booked';

            END IF;

        END
        """)

        booking_cursor.execute("DROP PROCEDURE IF EXISTS GetBookingDetails")
        booking_cursor.execute("""
        CREATE PROCEDURE GetBookingDetails()
        BEGIN

            SELECT

                Customer.name,
                Customer.email,
                Customer.phone,

                Movie.title AS movie_name,

                Booking.seat_number,
                Booking.booking_date

            FROM Booking

            JOIN Customer
            ON Booking.customer_id = Customer.customer_id

            JOIN Movie
            ON Booking.movie_id = Movie.movie_id;

        END
        """)

        conn.commit()
        booking_sql_objects_ready = True

    except mysql.connector.Error:

        conn.rollback()

    finally:

        booking_cursor.close()


def fetch_admin_movies():

    ensure_admin_tables()

    movie_cursor = db_cursor(dictionary=True)
    movie_cursor.execute("SELECT * FROM AdminMovie ORDER BY id DESC")
    movies = movie_cursor.fetchall()
    movie_cursor.close()

    return movies


def fetch_table(table_name):

    table_cursor = db_cursor()
    table_cursor.execute(f"SELECT * FROM {table_name} ORDER BY 1 DESC LIMIT 100")
    columns = [column[0] for column in table_cursor.description]
    rows = table_cursor.fetchall()
    table_cursor.close()

    return {
        "columns": columns,
        "rows": rows
    }


def fetch_booking_details():

    ensure_booking_sql_objects()

    booking_cursor = db_cursor()

    try:

        booking_cursor.execute("CALL GetBookingDetails()")
        columns = [column[0] for column in booking_cursor.description]
        rows = booking_cursor.fetchall()

        while booking_cursor.nextset():

            pass

    except mysql.connector.Error:

        booking_cursor.close()

        return fetch_table('Booking')

    booking_cursor.close()

    return {
        "columns": columns,
        "rows": rows
    }


def fetch_seat_inventory():

    ensure_admin_tables()

    seat_cursor = db_cursor(dictionary=True)
    seat_cursor.execute("""
    SELECT * FROM SeatInventory
    ORDER BY created_at DESC, id DESC
    LIMIT 200
    """)
    seats = seat_cursor.fetchall()
    seat_cursor.close()

    return seats


def split_seats(seat_number):

    return [
        seat.strip().upper()
        for seat in seat_number.split(",")
        if seat.strip()
    ]


def get_unavailable_seats(movie_id, theatre, show_date, show_time):

    ensure_admin_tables()

    seat_cursor = db_cursor(dictionary=True)
    seat_cursor.execute("""
    SELECT seat_number, status
    FROM SeatInventory
    WHERE movie_id=%s
      AND theatre=%s
      AND show_date=%s
      AND show_time=%s
    """, (movie_id, theatre, show_date, show_time))

    seats = seat_cursor.fetchall()
    seat_cursor.close()

    return seats


def save_uploaded_poster(file_storage):

    if not file_storage or file_storage.filename == "":

        return ""

    original_name = secure_filename(file_storage.filename)
    extension = os.path.splitext(original_name)[1]
    saved_name = f"{uuid.uuid4().hex}{extension}"
    save_path = os.path.join(app.config["UPLOAD_FOLDER"], saved_name)

    file_storage.save(save_path)

    return f"uploads/{saved_name}"


def is_admin_email(email):

    return (email or "").lower() in ADMIN_EMAILS


def password_is_hashed(stored_password):

    return stored_password and (
        stored_password.startswith("scrypt:")
        or stored_password.startswith("pbkdf2:")
    )


def is_admin_session():

    return session.get("role") == "admin" and is_admin_email(session.get("email"))

# HOME PAGE

@app.route('/')
def home():

    if 'email' not in session:

        return redirect('/login')

    page_cursor = db_cursor()

    page_cursor.execute("SELECT * FROM Movie")

    movies = page_cursor.fetchall()

    page_cursor.close()

    admin_movies = fetch_admin_movies()

    return render_template(
        'index.html',
        movies=movies,
        admin_movies=admin_movies,
        is_logged_in='email' in session,
        is_admin=is_admin_session(),
        current_user=session.get('user')
    )


# LOGIN PAGE

@app.route('/login')
def login():

    next_page = request.args.get('next', '')
    error = None

    if next_page == 'booking':

        error = 'Please login first to book tickets.'

    elif next_page == 'admin':

        error = 'Owner login required for admin access.'

    return render_template(
        'login.html',
        error=error,
        next_page=next_page
    )


# SIGNUP PAGE

@app.route('/signup')
def signup():

    return render_template('signup.html')


# ADMIN DASHBOARD
@app.route('/admin')
def admin_dashboard():

    if not is_admin_session():

        return redirect('/login?next=admin')

    ensure_admin_tables()
    ensure_booking_sql_objects()

    return render_template(
        'admin.html',
        admin_movies=fetch_admin_movies(),
        bookings=fetch_booking_details(),
        users=fetch_table('users'),
        seats=fetch_seat_inventory()
    )

 

# ADD MOVIE FROM ADMIN PANEL

@app.route('/admin/movie/add', methods=['POST'])
def admin_add_movie():

    if not is_admin_session():

        return redirect('/login?next=admin')

    ensure_admin_tables()

    poster_path = save_uploaded_poster(request.files.get('poster'))

    add_cursor = db_cursor()
    add_cursor.execute("""
    INSERT INTO AdminMovie (
        title,
        genre,
        language,
        format_type,
        duration,
        cast,
        director,
        rating,
        description,
        poster,
        trailer
    )
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    """, (
        request.form.get('title'),
        request.form.get('genre'),
        request.form.get('language'),
        request.form.get('format_type'),
        request.form.get('duration'),
        request.form.get('cast'),
        request.form.get('director'),
        request.form.get('rating'),
        request.form.get('description'),
        poster_path,
        request.form.get('trailer')
    ))

    conn.commit()
    add_cursor.close()

    return redirect('/admin')


# DELETE ADMIN MOVIE

@app.route('/admin/movie/delete/<int:movie_id>', methods=['POST'])
def admin_delete_movie(movie_id):

    if not is_admin_session():

        return redirect('/login?next=admin')

    ensure_admin_tables()

    delete_cursor = db_cursor()
    delete_cursor.execute(
        "DELETE FROM AdminMovie WHERE id=%s",
        (movie_id,)
    )
    conn.commit()
    delete_cursor.close()

    return redirect('/admin')


# MANAGE SEATS FROM ADMIN PANEL

@app.route('/admin/seats/add', methods=['POST'])
def admin_add_seat_status():

    if not is_admin_session():

        return redirect('/login?next=admin')

    ensure_admin_tables()

    seat_cursor = db_cursor()

    try:

        seat_cursor.execute("""
        INSERT INTO SeatInventory (
            movie_id,
            theatre,
            show_date,
            show_time,
            seat_number,
            status,
            booking_ref
        )
        VALUES (%s, %s, %s, %s, %s, %s, 'admin')
        ON DUPLICATE KEY UPDATE
            status=VALUES(status),
            booking_ref='admin'
        """, (
            request.form.get('movie_id'),
            request.form.get('theatre'),
            request.form.get('show_date'),
            request.form.get('show_time'),
            request.form.get('seat_number').upper(),
            request.form.get('status', 'blocked')
        ))

        conn.commit()

    finally:

        seat_cursor.close()

    return redirect('/admin')


@app.route('/admin/seats/delete/<int:seat_id>', methods=['POST'])
def admin_delete_seat_status(seat_id):

    if not is_admin_session():

        return redirect('/login?next=admin')

    ensure_admin_tables()

    seat_cursor = db_cursor()
    seat_cursor.execute(
        "DELETE FROM SeatInventory WHERE id=%s",
        (seat_id,)
    )
    conn.commit()
    seat_cursor.close()

    return redirect('/admin')


# REAL-TIME SEAT AVAILABILITY

@app.route('/api/seats')
def api_seats():

    seats = get_unavailable_seats(
        request.args.get('movie_id', ''),
        request.args.get('theatre', ''),
        request.args.get('show_date', ''),
        request.args.get('show_time', '')
    )

    return jsonify({
        'seats': seats,
        'unavailable': [seat['seat_number'] for seat in seats]
    })


# REGISTER USER

@app.route('/register', methods=['POST'])
def register():

    global cursor
    ensure_user_table_security()
    cursor = db_cursor()

    username = request.form['username'].strip()
    email = request.form['email'].strip().lower()
    password = request.form['password']

    cursor.execute(
        "SELECT username, email FROM users WHERE username=%s OR email=%s LIMIT 1",
        (username, email)
    )

    existing_user = cursor.fetchone()

    if existing_user:

        if existing_user[0] == username:

            message = 'Username already exists. Please choose another username.'

        else:

            message = 'Email already exists. Please login or use another email.'

        return render_template(
            'signup.html',
            error=message,
            username=username,
            email=email
        )

    query = """
    INSERT INTO users(username, email, password)
    VALUES(%s, %s, %s)
    """

    values = (username, email, generate_password_hash(password))

    try:

        cursor.execute(query, values)
        conn.commit()

        return render_template(
            'login.html',
            error='Account created successfully. Please login.',
            next_page=''
        )

    except mysql.connector.Error:

        conn.rollback()

        return render_template(
            'signup.html',
            error='Signup failed. Username or email may already exist.',
            username=username,
            email=email
        )


# LOGIN USER

@app.route('/login_user', methods=['POST'])
def login_user():

    global cursor
    ensure_user_table_security()
    cursor = db_cursor()

    email = request.form['email'].strip().lower()
    password = request.form['password']
    next_page = request.form.get('next', '')

    if is_admin_email(email) and password == ADMIN_PASSWORD:

        session['user'] = 'Owner'
        session['email'] = email
        session['role'] = 'admin'

        return redirect('/admin')

    query = """
    SELECT * FROM users
    WHERE email=%s
    """

    values = (email,)

    cursor.execute(query, values)

    user = cursor.fetchone()

    password_matches = False

    if user:

        stored_password = user[3]

        if password_is_hashed(stored_password):

            password_matches = check_password_hash(stored_password, password)

        else:

            password_matches = stored_password == password

            if password_matches:

                cursor.execute(
                    "UPDATE users SET password=%s WHERE email=%s",
                    (generate_password_hash(password), email)
                )
                conn.commit()

    if user and password_matches:

        session['user'] = user[1]
        session['email'] = user[2]
        session['role'] = 'admin' if is_admin_email(user[2]) else 'user'

        if session['role'] == 'admin':

            return redirect('/admin')

        if next_page == 'booking':

            return redirect('/#booking')

        return redirect('/')

    else:

        return render_template(
            'login.html',
            error='User is not registered or password is wrong. Please retry.',
            next_page=next_page
        )


# LOGOUT

@app.route('/logout')
def logout():

    session.clear()

    return redirect('/')

# USER PROFILE

@app.route('/profile')
def profile():

    if 'email' not in session:

        return redirect('/login')

    profile_cursor = db_cursor(dictionary=True)

    query = """
    SELECT

        Customer.name,
        Customer.email,
        Customer.phone,

        Movie.title AS movie_name,

        Booking.seat_number

    FROM Booking

    JOIN Customer
    ON Booking.customer_id = Customer.customer_id

    JOIN Movie
    ON Booking.movie_id = Movie.movie_id

    WHERE Customer.email=%s
    """

    profile_cursor.execute(
        query,
        (session['email'],)
    )

    bookings = profile_cursor.fetchall()

    profile_cursor.close()

    return render_template(
        'profile.html',
        bookings=bookings,
        user=session.get('user'),
        email=session.get('email'),
        is_admin=is_admin_session()
    )
# BOOKING

@app.route('/book', methods=['POST'])
def book():
    if 'email' not in session:

        return redirect('/login?next=booking')
    global cursor
    ensure_booking_sql_objects()
    cursor = db_cursor()

    name = request.form['name']
    email = request.form['email']
    phone = request.form['phone']
    movie_id = request.form['movie_id']
    seat_number = request.form['seat_number']
    movie_name = request.form.get('movie_name', f'Movie #{movie_id}')
    total_amount = request.form.get('total_amount', '150')
    payment_method = request.form.get('payment_method', 'UPI')
    raw_theatre = request.form.get('theatre', '')
    raw_show_time = request.form.get('show_time', '')

    theatre_map = {
        '1': 'PVR Orion - Rajajinagar',
        '2': 'INOX Garuda - Magrath Rd',
        '3': 'Cinepolis Forum - Koramangala',
        '4': 'PVR VR Bengaluru - Whitefield',
        '5': 'IMAX Science City - Domlur'
    }

    time_map = {
        '10:00': '10:00 AM - Morning Show',
        '13:30': '1:30 PM - Matinee',
        '17:00': '5:00 PM - Evening Show',
        '20:30': '8:30 PM - Night Show',
        '23:30': '11:30 PM - Late Night'
    }

    theatre = theatre_map.get(
        raw_theatre,
        raw_theatre or 'Namma Cinema'
    )

    show_date = request.form.get('show_date', 'Today')
    show_time = time_map.get(
        raw_show_time,
        raw_show_time or 'Selected Show'
    )
    requested_seats = split_seats(seat_number)

    if not requested_seats:

        booking = {
            'booking_id': 'No seat selected',
            'name': name,
            'email': email,
            'phone': phone,
            'movie_name': movie_name,
            'theatre': theatre,
            'show_date': show_date,
            'show_time': show_time,
            'seat_number': 'None',
            'total_amount': total_amount,
            'payment_method': payment_method,
            'error_message': 'Please select a seat before payment.'
        }

        return render_template(
            'booking_confirmation.html',
            booking=booking,
            failed=True
        )

    unavailable = get_unavailable_seats(
        movie_id,
        raw_theatre,
        show_date,
        raw_show_time
    )
    unavailable_numbers = {
        seat['seat_number']
        for seat in unavailable
    }
    conflicting_seats = [
        seat
        for seat in requested_seats
        if seat in unavailable_numbers
    ]

    if conflicting_seats:

        booking = {
            'booking_id': 'Seat unavailable',
            'name': name,
            'email': email,
            'phone': phone,
            'movie_name': movie_name,
            'theatre': theatre,
            'show_date': show_date,
            'show_time': show_time,
            'seat_number': ', '.join(conflicting_seats),
            'total_amount': total_amount,
            'payment_method': payment_method,
            'error_message': 'Selected seat is already booked. Please choose another seat.'
        }

        return render_template(
            'booking_confirmation.html',
            booking=booking,
            failed=True
        )

    try:

        customer_query = """
        INSERT INTO Customer(name, email, phone)
        VALUES (%s, %s, %s)
        """

        customer_values = (name, email, phone)

        cursor.execute(customer_query, customer_values)
        conn.commit()

        customer_id = cursor.lastrowid

        booking_query = """
        INSERT INTO Booking(customer_id, movie_id, seat_number)
        VALUES (%s, %s, %s)
        """

        booking_values = (
            customer_id,
            movie_id,
            seat_number
        )

        cursor.execute(
            booking_query,
            booking_values
        )

        booking_id = cursor.lastrowid or customer_id

        for seat in requested_seats:

            cursor.execute("""
            INSERT INTO SeatInventory (
                movie_id,
                theatre,
                show_date,
                show_time,
                seat_number,
                status,
                booking_ref
            )
            VALUES (%s, %s, %s, %s, %s, 'booked', %s)
            """, (
                movie_id,
                raw_theatre,
                show_date,
                raw_show_time,
                seat,
                f'NC{booking_id:06d}'
            ))

        conn.commit()

        booking = {
            'booking_id': f'NC{booking_id:06d}',
            'name': name,
            'email': email,
            'phone': phone,
            'movie_name': movie_name,
            'theatre': theatre,
            'show_date': show_date,
            'show_time': show_time,
            'seat_number': seat_number,
            'total_amount': total_amount,
            'payment_method': payment_method
        }

        return render_template(
            'booking_confirmation.html',
            booking=booking,
            failed=False
        )

    except IntegrityError:

        conn.rollback()

        booking = {
            'booking_id': 'Seat unavailable',
            'name': name,
            'email': email,
            'phone': phone,
            'movie_name': movie_name,
            'theatre': theatre,
            'show_date': show_date,
            'show_time': show_time,
            'seat_number': seat_number,
            'total_amount': total_amount,
            'payment_method': payment_method,
            'error_message': 'Another user just booked this seat. Please retry with a different seat.'
        }

        return render_template(
            'booking_confirmation.html',
            booking=booking,
            failed=True
        )

    except:

        conn.rollback()

        booking = {
            'booking_id': 'Not generated',
            'name': name,
            'email': email,
            'phone': phone,
            'movie_name': movie_name,
            'theatre': theatre,
            'show_date': show_date,
            'show_time': show_time,
            'seat_number': seat_number,
            'total_amount': total_amount,
            'payment_method': payment_method,
            'error_message': 'Booking failed. Please check database connection and retry.'
        }

        return render_template(
            'booking_confirmation.html',
            booking=booking,
            failed=True
        )


if __name__ == '__main__':

    ensure_admin_tables()
    ensure_user_table_security()
    ensure_booking_sql_objects()

    app.run(debug=True, threaded=False)
