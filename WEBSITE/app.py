from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
# added werkzueg security for password protection, need to add administrator to manage accounts, stocks, etc
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__, template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:password@localhost/project_stocks'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'my-secret-key'

db = SQLAlchemy(app)

# we need to maybe adapt these into the SQL database rather than here in the site
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    display_name = db.Column(db.String(150), nullable=False, default="New User")
    password_hash = db.Column(db.String(512), nullable=False)
    funds = db.Column(db.Float, default=100000.0)
    portfolio = db.relationship('Portfolio', backref='owner', lazy=True)

class StockInventory(db.Model):
    __tablename__ = "StockInventory"
    stockId = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), nullable=False)
    ticker = db.Column(db.String(5), unique=True, nullable=False)
    quantity = db.Column(db.Integer, default=0)
    initStockPrice = db.Column(db.Float)
    currentMarketPrice = db.Column(db.Float)

class Portfolio(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('StockInventory.stockId'), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    stock = db.relationship('StockInventory')

with app.app_context():
    db.create_all()


@app.route('/')
def home():
    return render_template('home.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash("Username already exists.")
            return redirect(url_for('register'))

        password_hash = generate_password_hash(password)
        user = User(username=username, password_hash=password_hash)
        db.session.add(user)
        db.session.commit()
        flash("Account created successfully! Please log in.")
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        user = User.query.filter_by(username=username).first()
        if user and check_password_hash(user.password_hash, password):
            session['user_id'] = user.id
            flash("Logged in successfully!")
            return redirect(url_for('portfolio'))
        else:
            flash("Invalid username or password.")
            return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    flash("Logged out successfully!")
    return redirect(url_for('home'))

@app.route('/portfolio')
def portfolio():
    if 'user_id' not in session:
        flash("Please log in first.")
        return redirect(url_for('login'))
    user = User.query.get(session['user_id'])
    user_portfolio = Portfolio.query.filter_by(user_id=user.id).all()
    return render_template('portfolio.html', portfolio=user_portfolio, user=user, current_user=user )

@app.route('/market')
def market():
    stocks = StockInventory.query.all()
    return render_template('market.html', stocks=stocks)

@app.route('/trade/<ticker>', methods=['GET', 'POST'])
def trade(ticker):
    if 'user_id' not in session:
        flash("Please log in first.")
        return redirect(url_for('login'))

    stock = StockInventory.query.filter_by(ticker=ticker.upper()).first()
    if not stock:
        flash(f"Stock '{ticker}' not found.")
        return redirect(url_for('market'))

    user = User.query.get(session['user_id'])

    try:
        quantity = int(request.args.get('quantity') or request.form.get('quantity') or 0)
    except (ValueError, TypeError):
        quantity = 0

    action = request.args.get('action') or request.form.get('action')

    if request.method == 'POST':
        if not quantity or not action:
            flash("Invalid quantity or action.")
            return redirect(url_for('portfolio'))

        total = stock.currentMarketPrice * quantity

        if action == "BUY":
            if user.funds >= total and stock.quantity >= quantity:
                user.funds -= total
                stock.quantity -= quantity
                p_stock = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
                if p_stock:
                    p_stock.quantity += quantity
                else:
                    new_portfolio = Portfolio(user_id=user.id, stock_id=stock.stockId, quantity=quantity)
                    db.session.add(new_portfolio)
                db.session.commit()
                flash(f"Bought {quantity} shares of {stock.ticker}")
            else:
                flash("Insufficient funds or stock quantity.")

        elif action == "SELL":
            p_stock = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
            if p_stock and p_stock.quantity >= quantity:
                user.funds += total
                stock.quantity += quantity
                p_stock.quantity -= quantity
                if p_stock.quantity == 0:
                    db.session.delete(p_stock)
                db.session.commit()
                flash(f"Sold {quantity} shares of {stock.ticker}")
            else:
                flash("Not enough shares to sell.")

        return redirect(url_for('portfolio'))

    return render_template('trade.html', stock=stock, quantity=quantity, action=action)

if __name__ == '__main__':
    app.run(debug=True)
