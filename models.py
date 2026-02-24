from flask_sqlalchemy import SQLAlchemy

# Initialize the database object
db = SQLAlchemy()

# 1. User Model (Replaces your Users Table)
class User(db.Model):
    __tablename__ = 'users'
    
    id = db.Column(db.Integer, primary_key=True)
    # We use access_code as a unique identifier for login
    access_code = db.Column(db.String(50), unique=True, nullable=False)
    role = db.Column(db.String(20), nullable=False)  # 'admin', 'provider', etc.
    name = db.Column(db.String(100), nullable=False)

# 2. Mapping Model (Replaces your Payer Mappings Table)
class PayerMapping(db.Model):
    __tablename__ = 'payer_mappings'
    
    id = db.Column(db.Integer, primary_key=True)
    # The raw name coming from the PDF (e.g., "UHC Medicare")
    pdf_payer_name = db.Column(db.String(255), unique=True, nullable=False)
    # The cleaner system name (e.g., "UnitedHealthcare")
    system_payer_name = db.Column(db.String(255), nullable=False)


class Patient(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(100))
    last_name = db.Column(db.String(100))
    dob = db.Column(db.String(20))
    member_id = db.Column(db.String(50))
    payer_name = db.Column(db.String(200))
    status = db.Column(db.String(50))  # 'verified', 'error', 'manual_review'
    
    # Financials stored as simple strings for display
    copay = db.Column(db.String(50), default="$0.00")
    coins = db.Column(db.String(50), default="0%")
    deductible_rem = db.Column(db.String(50), default="$0.00")
    oop_rem = db.Column(db.String(50), default="$0.00")
    
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())