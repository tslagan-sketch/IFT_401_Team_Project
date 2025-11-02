from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, time, timedelta, date
import random




app = Flask(__name__, template_folder='templates')
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+pymysql://root:password@localhost/project_stocks'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = 'my-secret-key'
app.config['ADMIN_CONFIRM_CODE'] = 'SECRET_ADMIN_CODE'

db = SQLAlchemy(app)


class User(db.Model):
    __tablename__ = 'user'
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(150), unique=True, nullable=False)
    email = db.Column(db.String(255))
    display_name = db.Column(db.String(150), default="New User")
    password_hash = db.Column(db.String(512), nullable=False)
    funds = db.Column(db.Float, default=100000.0)
    role = db.Column(db.String(20), nullable=False, default='user')
    portfolio = db.relationship('Portfolio', backref='owner', lazy=True, cascade="all, delete-orphan")
    orders = db.relationship('Order', backref='user', lazy=True, cascade="all, delete-orphan")
    def is_admin(self):
        return self.role == 'admin'


class StockInventory(db.Model):
    __tablename__ = 'StockInventory'

    stockId = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100))
    ticker = db.Column(db.String(10), unique=True, nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    base_price = db.Column(db.Float, default=0.0)
    current_price = db.Column(db.Float, default=0.0)
    day_high = db.Column(db.Float)
    day_low = db.Column(db.Float)
    currentMarketPrice = db.Column(db.Float)


class Portfolio(db.Model):
    __tablename__ = "portfolio"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('StockInventory.stockId'), nullable=False)
    quantity = db.Column(db.Integer, default=0)
    stock = db.relationship('StockInventory')

class Order(db.Model):
    __tablename__ = "orders"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id', ondelete="CASCADE"), nullable=False)
    stock_id = db.Column(db.Integer, db.ForeignKey('StockInventory.stockId'), nullable=False)
    action = db.Column(db.String(10), nullable=False)
    quantity = db.Column(db.Integer, nullable=False)
    price_per_stock = db.Column(db.Float, nullable=True)
    total_amount = db.Column(db.Float, nullable=True)
    status = db.Column(db.String(20), nullable=False, default='pending')
    timestamp = db.Column(db.DateTime, server_default=db.func.now())
    executed_at = db.Column(db.DateTime, nullable=True)
    stock = db.relationship('StockInventory')

class StockPriceTick(db.Model):
    __tablename__ = "stock_price_ticks"
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('StockInventory.stockId'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    price = db.Column(db.Float, nullable=False)

class DailyPriceSummary(db.Model):
    __tablename__ = "daily_price_summary"
    id = db.Column(db.Integer, primary_key=True)
    stock_id = db.Column(db.Integer, db.ForeignKey('StockInventory.stockId'), nullable=False)
    day = db.Column(db.Date, nullable=False, index=True)
    open_price = db.Column(db.Float)
    high_price = db.Column(db.Float)
    low_price = db.Column(db.Float)
    close_price = db.Column(db.Float)

class CalendarEvent(db.Model):
    __tablename__ = "calendar_event"
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    description = db.Column(db.Text)
    start_datetime = db.Column(db.DateTime, nullable=False)
    end_datetime = db.Column(db.DateTime, nullable=True)
    created_by = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    event_type = db.Column(db.Enum('event','closed','custom_hours', name='event_type_enum'), default='event')
    custom_open_time = db.Column(db.Time, nullable=True)
    custom_close_time = db.Column(db.Time, nullable=True)

with app.app_context():
    db.create_all()

MARKET_OPEN = time(8, 0)
MARKET_CLOSE = time(17, 0)
MIN_TICK_SECONDS = 60
MAX_TICK_PERCENT = 0.02

HARDCODED_HOLIDAYS = {
    date(2025, 1, 1),
    date(2025, 12, 25),
}

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in to continue.")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        user = User.query.get(session['user_id'])
        if not user or user.role != 'admin':
            flash("Admin access required.")
            return redirect(url_for('profile'))
        return f(*args, **kwargs)
    return decorated

@app.context_processor
def inject_user():
    if 'user_id' in session:
        return {'current_user': User.query.get(session['user_id'])}
    return {'current_user': None}

def market_open(now=None):
    if now is None:
        now = datetime.now()
    if now.weekday() >= 5 or now.date() in HARDCODED_HOLIDAYS:
        return False
    if not (MARKET_OPEN <= now.time() <= MARKET_CLOSE):
        return False
    events = CalendarEvent.query.filter(CalendarEvent.start_datetime <= now).all()
    for e in events:
        if e.end_datetime is None or e.end_datetime >= now:
            return False
    return True

def last_tick_for_stock(stock_id):
    return StockPriceTick.query.filter_by(stock_id=stock_id).order_by(StockPriceTick.timestamp.desc()).first()


def add_stock(name, ticker, quantity, base_price):
    new_stock = StockInventory(
        name=name,
        ticker=ticker,
        quantity=quantity,
        initStockPrice=base_price,
        currentMarketPrice=base_price,
        day_high=base_price,
        day_low=base_price
    )
    db.session.add(new_stock)
    db.session.commit()
    
    
def add_price_tick_if_allowed(stock, new_price):
    last = last_tick_for_stock(stock.stockId)
    now = datetime.utcnow()
    if last is None or (now - last.timestamp).total_seconds() >= MIN_TICK_SECONDS:
        tick = StockPriceTick(stock_id=stock.stockId, timestamp=now, price=new_price)
        db.session.add(tick)
        db.session.commit()

def compress_day_for_stock(stock_id, day=None):
    if day is None:
        day = date.today() - timedelta(days=1)
    start_dt = datetime.combine(day, time(0,0))
    end_dt = datetime.combine(day, time(23,59,59))
    ticks = StockPriceTick.query.filter(
        StockPriceTick.stock_id==stock_id,
        StockPriceTick.timestamp >= start_dt,
        StockPriceTick.timestamp <= end_dt
    ).order_by(StockPriceTick.timestamp.asc()).all()
    if not ticks:
        return None
    prices = [t.price for t in ticks]
    summary = DailyPriceSummary(
        stock_id=stock_id,
        day=day,
        open_price=prices[0],
        high_price=max(prices),
        low_price=min(prices),
        close_price=prices[-1]
    )
    db.session.add(summary)
    StockPriceTick.query.filter(
        StockPriceTick.stock_id==stock_id,
        StockPriceTick.timestamp >= start_dt,
        StockPriceTick.timestamp <= end_dt
    ).delete()
    db.session.commit()
    return summary

def get_avg_purchase_price(user_id, stock_id):
    buys = Order.query.filter_by(user_id=user_id, stock_id=stock_id, action='BUY', status='executed').all()
    total_qty = 0
    total_spent = 0.0
    for b in buys:
        if b.quantity and b.total_amount:
            total_qty += b.quantity
            total_spent += b.total_amount
    if total_qty == 0:
        return None
    return round(total_spent / total_qty, 2)

@app.route('/')
def home():
    stocks = StockInventory.query.limit(5).all()
    return render_template('home.html', stocks=stocks)

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        if User.query.filter_by(username=username).first():
            flash("Username already exists.")
            return redirect(url_for('register'))
        password_hash = generate_password_hash(password)
        admin_code = request.form.get('admin_code')
        role = 'admin' if admin_code and admin_code == app.config.get('ADMIN_CONFIRM_CODE') else 'user'
        user = User(username=username, password_hash=password_hash, role=role)
        db.session.add(user)
        db.session.commit()
        flash("Account created successfully.")
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
            return redirect(url_for('profile'))
        flash("Invalid credentials.")
        return redirect(url_for('login'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('user_id', None)
    return redirect(url_for('home'))

@app.route("/profile")
@login_required
def profile():
    user = User.query.get(session['user_id'])
    if not user:
        flash("User not found.")
        return redirect(url_for('login'))

    portfolio = Portfolio.query.filter_by(user_id=user.id).all()

    orders = Order.query.filter_by(user_id=user.id).order_by(Order.timestamp.desc()).all()

    return render_template("profile.html", portfolio=portfolio, orders=orders)

@app.route('/admin')
@admin_required
def admin_console():
    stocks = StockInventory.query.all()
    users = User.query.all()
    calendar_events = CalendarEvent.query.order_by(CalendarEvent.start_datetime.asc()).all()
    orders = Order.query.order_by(Order.timestamp.desc()).all()
    return render_template('admin_console.html', stocks=stocks, users=users, calendar_events=calendar_events, orders=orders)


@app.route('/promote/<int:user_id>', methods=['POST'])
def promote_user(user_id):
    user = User.query.get(user_id)
    if user:
        user.role = 'admin'
        db.session.commit()
    return redirect(url_for('admin_console'))

def add_stock_to_db(name, ticker, quantity, base_price):
    new_stock = StockInventory(
        name=name,
        ticker=ticker,
        quantity=quantity,
        base_price=base_price,
        current_price=base_price,  # added current_price initialization
        day_high=base_price,
        day_low=base_price
    )
    db.session.add(new_stock)
    db.session.commit()


@app.route('/add_stock', methods=['POST'])
@admin_required
def add_stock_route():
    stock_name = request.form['stock_name']
    ticker = request.form['ticker']
    quantity = request.form['quantity']
    base_price = request.form['base_price']
    
    # Call the helper function to add the stock
    add_stock_to_db(stock_name, ticker, quantity, base_price)
    
    flash(f"Stock {ticker} added successfully!", "success")
    return redirect(url_for('admin_console'))



def add_price_tick_if_allowed(stock, new_price):
    # Assume `last_tick_for_stock(stock.stockId)` gets the most recent tick
    last = last_tick_for_stock(stock.stockId)  
    now = datetime.utcnow()

    # Check if enough time has passed since the last price tick
    if last is None or (now - last.timestamp).total_seconds() >= MIN_TICK_SECONDS:
        # If allowed, add a new price tick
        tick = StockPriceTick(stock_id=stock.stockId, timestamp=now, price=new_price)
        db.session.add(tick)
        db.session.commit()
        
        
@app.route('/remove_stock/<int:stock_id>', methods=['POST'])
@admin_required
def remove_stock(stock_id):
    s = StockInventory.query.get_or_404(stock_id)
    
    # Ensure no one owns this stock before removing it
    owners = Portfolio.query.filter_by(stock_id=s.stockId).first()
    
    if owners:
        flash("Cannot remove stock while users still hold positions.", "danger")
        return redirect(url_for('admin_console'))

    # Remove the stock from the inventory
    db.session.delete(s)
    db.session.commit()

    flash(f"Removed stock {s.ticker}.", "success")
    return redirect(url_for('admin_console'))

@app.route('/add_funds_user/<int:user_id>', methods=['POST'])
@admin_required
def add_funds_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        amount = float(request.form.get('amount', '0'))
    except ValueError:
        amount = 0
    if amount <= 0:
        flash("Provide a positive amount.", "danger")
        return redirect(url_for('admin_console'))
    user.funds += amount
    db.session.commit()
    flash(f"Added ${amount:.2f} to {user.username}.", "success")
    return redirect(url_for('admin_console'))

@app.route('/subtract_funds_user/<int:user_id>', methods=['POST'])
@admin_required
def subtract_funds_user(user_id):
    user = User.query.get_or_404(user_id)
    try:
        amount = float(request.form.get('amount', '0'))
    except ValueError:
        amount = 0
    if amount <= 0 or user.funds < amount:
        flash("Invalid amount or insufficient funds.", "danger")
        return redirect(url_for('admin_console'))
    user.funds -= amount
    db.session.commit()
    flash(f"Subtracted ${amount:.2f} from {user.username}.", "success")
    return redirect(url_for('admin_console'))

def market_demo_tick():
    stocks = StockInventory.query.all()
    for s in stocks:
        # ±5% random fluctuation
        pct_change = random.uniform(-0.05, 0.05)
        new_price = max(0.01, round(s.current_price * (1 + pct_change), 2))

        # Update high/low
        s.day_high = max(s.day_high or new_price, new_price)
        s.day_low = min(s.day_low or new_price, new_price)

        s.current_price = new_price

    db.session.commit()

    return jsonify([{
        "ticker": s.ticker,
        "name": s.name,
        "current_price": s.current_price,
        "open_price": s.base_price,
        "high_price": s.day_high,
        "low_price": s.day_low,
        "quantity": s.quantity,
        "market_cap": round(s.current_price * s.quantity, 2)
    } for s in stocks])
    
@app.route('/market')
def market():
    stocks = StockInventory.query.all()
    market_data = []
    for s in stocks:
        today_summary = DailyPriceSummary.query.filter_by(
            stock_id=s.stockId,
            day=date.today()
        ).first()
        open_price = today_summary.open_price if today_summary else s.base_price
        high_price = today_summary.high_price if today_summary else s.current_price
        low_price = today_summary.low_price if today_summary else s.current_price

        market_data.append({
            "name": s.name,
            "ticker": s.ticker,
            "current_price": s.current_price,
            "open_price": open_price,
            "high_price": high_price,
            "low_price": low_price,
            "quantity": s.quantity,
            "market_cap": round(s.current_price * s.quantity, 2)
        })
    return render_template('market.html', stocks=market_data)


@app.route('/market_demo_data')
def market_demo_data():
    try:
        stocks = StockInventory.query.all()  # <-- use StockInventory
        stock_list = []

        for s in stocks:
            # ±5% random fluctuation for demo
            pct_change = random.uniform(-0.05, 0.05)
            new_price = max(0.01, round(s.current_price * (1 + pct_change), 2))

            # Update high/low
            s.day_high = max(s.day_high or new_price, new_price)
            s.day_low = min(s.day_low or new_price, new_price)
            s.current_price = new_price

            stock_list.append({
                'name': s.name,
                'ticker': s.ticker,
                'current_price': s.current_price,
                'open_price': s.base_price,
                'high_price': s.day_high,
                'low_price': s.day_low,
                'quantity': s.quantity,
                'market_cap': round(s.current_price * s.quantity, 2)
            })

        db.session.commit()
        return jsonify(stock_list)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    
@app.route('/trade/<ticker>', methods=['GET', 'POST'])
@login_required
def trade(ticker):
    stock = StockInventory.query.filter_by(ticker=ticker.upper()).first()
    if not stock:
        flash("Stock not found.")
        return redirect(url_for('market'))
    user = User.query.get(session['user_id'])
    if request.method == 'POST':
        if not market_open():
            flash("Market is closed. Trades are allowed only during market hours and non-closed days.", "danger")
            return redirect(url_for('trade', ticker=ticker))
        try:
            quantity = int(request.form.get('quantity', 0))
        except:
            quantity = 0
        action = request.form.get('action').upper()
        total = round(stock.current_price * quantity, 2)  # Fixed to use current_price
        if quantity <= 0:
            flash("Quantity must be > 0", "danger")
            return redirect(url_for('trade', ticker=ticker))
        if action == "BUY":
            if user.funds >= total and stock.quantity >= quantity:
                user.funds -= total
                stock.quantity -= quantity
                p = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
                if p:
                    p.quantity += quantity
                else:
                    p = Portfolio(user_id=user.id, stock_id=stock.stockId, quantity=quantity)
                    db.session.add(p)
                order = Order(user_id=user.id, stock_id=stock.stockId, action='BUY', quantity=quantity, price_per_stock=stock.current_price, total_amount=total, status='executed', executed_at=datetime.utcnow())
                db.session.add(order)
                db.session.commit()
                flash(f"Bought {quantity} shares of {stock.ticker} at ${stock.current_price:.2f}.", "success")
            else:
                flash("Insufficient funds or not enough stock available.", "danger")
        elif action == "SELL":
            p = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
            if not p or p.quantity < quantity:
                flash("Not enough shares to sell.", "danger")
            else:
                proceeds = round(stock.current_price * quantity, 2)  # Fixed to use current_price
                user.funds += proceeds
                stock.quantity += quantity
                p.quantity -= quantity
                if p.quantity == 0:
                    db.session.delete(p)
                order = Order(user_id=user.id, stock_id=stock.stockId, action='SELL', quantity=quantity, price_per_stock=stock.current_price, total_amount=proceeds, status='executed', executed_at=datetime.utcnow())
                db.session.add(order)
                db.session.commit()
                flash(f"Sold {quantity} shares of {stock.ticker} at ${stock.current_price:.2f}.", "success")
        else:
            flash("Invalid action.", "danger")
        return redirect(url_for('profile'))
    p = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
    owned_qty = p.quantity if p else 0
    avg_price = get_avg_purchase_price(user.id, stock.stockId) if p else None
    return render_template('trade.html', stock=stock, owned_qty=owned_qty, avg_price=avg_price)

@app.route('/order_preview/<ticker>', methods=['POST'])
@login_required
def order_preview(ticker):
    stock = StockInventory.query.filter_by(ticker=ticker).first_or_404()
    action = request.form.get("action")
    try:
        quantity = int(request.form.get("quantity"))
    except:
        flash("Invalid quantity.", "danger")
        return redirect(url_for('trade', ticker=ticker))
    if quantity <= 0:
        flash("Quantity must be greater than zero.", "danger")
        return redirect(url_for('trade', ticker=ticker))
    total = stock.currentMarketPrice * quantity
    if action == 'BUY' and not market_open():
        flash("Market closed. Cannot place buy orders now.", "danger")
        return redirect(url_for('market'))
    return render_template("order_preview.html", stock=stock, action=action, quantity=quantity, total=total)

@app.route('/execute_order/<ticker>', methods=['POST'])
@login_required
def execute_order(ticker):
    user = User.query.get(session['user_id'])
    if not user.email:
        flash("You must set an email address before trading stocks.", "danger")
        return redirect(url_for('profile'))
    stock = StockInventory.query.filter_by(ticker=ticker).first_or_404()
    action = request.form['action']
    try:
        quantity = int(request.form['quantity'])
    except:
        flash("Invalid quantity.", "danger")
        return redirect(url_for('trade', ticker=ticker))
    if not market_open():
        flash("Market closed. Cannot execute orders now.", "danger")
        return redirect(url_for('trade', ticker=ticker))
    total = stock.currentMarketPrice * quantity
    if action.upper() == "BUY":
        if user.funds < total:
            flash("Not enough funds.", "danger")
            return redirect(url_for('trade', ticker=ticker))
        if stock.quantity < quantity:
            flash("Not enough stock available.", "danger")
            return redirect(url_for('trade', ticker=ticker))
        user.funds -= total
        stock.quantity -= quantity
        portfolio_item = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
        if portfolio_item:
            portfolio_item.quantity += quantity
        else:
            portfolio_item = Portfolio(user_id=user.id, stock_id=stock.stockId, quantity=quantity)
            db.session.add(portfolio_item)
    elif action.upper() == "SELL":
        portfolio_item = Portfolio.query.filter_by(user_id=user.id, stock_id=stock.stockId).first()
        if not portfolio_item or portfolio_item.quantity < quantity:
            flash("Not enough shares to sell.", "danger")
            return redirect(url_for('trade', ticker=ticker))
        portfolio_item.quantity -= quantity
        user.funds += total
        stock.quantity += quantity
        if portfolio_item.quantity == 0:
            db.session.delete(portfolio_item)
    order = Order(user_id=user.id, stock_id=stock.stockId, action=action.upper(), quantity=quantity, price_per_stock=stock.currentMarketPrice, total_amount=total, status='executed', executed_at=datetime.utcnow())
    db.session.add(order)
    db.session.commit()
    flash(f"{action} order confirmed for {quantity} shares of {stock.ticker}.", "success")
    return redirect(url_for('order_confirmation', order_id=order.id))

@app.route('/order_confirmation/<int:order_id>')
@login_required
def order_confirmation(order_id):
    order = Order.query.get_or_404(order_id)
    if order.user_id != session['user_id'] and not User.query.get(session['user_id']).is_admin():
        flash("You cannot view this order.")
        return redirect(url_for('profile'))
    return render_template("order_confirmation.html", order=order)

import random

@app.route("/simulate_fast_ticks", methods=["POST"])
def simulate_fast_ticks():
    stocks = StockInventory.query.all()
    for stock in stocks:

        change = stock.base_price * random.uniform(-0.02, 0.02)
        stock.current_price = round(stock.base_price + change, 2)
        if not stock.day_high or stock.current_price > stock.day_high:
            stock.day_high = stock.current_price
        if not stock.day_low or stock.current_price < stock.day_low:
            stock.day_low = stock.current_price

    db.session.commit()
    return redirect(url_for("admin_console"))


@app.route('/price_update/<ticker>', methods=['GET'])
def price_update(ticker):
    s = StockInventory.query.filter_by(ticker=ticker.upper()).first_or_404()
    ticks = StockPriceTick.query.filter_by(stock_id=s.stockId).order_by(StockPriceTick.timestamp.asc()).all()
    if ticks:
        data = [{'ts': t.timestamp.isoformat(), 'price': t.price} for t in ticks]
    else:
        summaries = DailyPriceSummary.query.filter_by(stock_id=s.stockId).order_by(DailyPriceSummary.day.asc()).all()
        data = [{'day': d.day.isoformat(), 'open': d.open_price, 'high': d.high_price, 'low': d.low_price, 'close': d.close_price} for d in summaries]
    return jsonify({'ticker': s.ticker, 'current_price': s.currentMarketPrice, 'data': data})

@app.route('/price_history/<ticker>', methods=['GET'])
def price_history(ticker):
    typ = request.args.get('type', 'minute')
    limit = int(request.args.get('limit', 500))
    s = StockInventory.query.filter_by(ticker=ticker.upper()).first_or_404()
    if typ == 'daily':
        sums = DailyPriceSummary.query.filter_by(stock_id=s.stockId).order_by(DailyPriceSummary.day.asc()).limit(limit).all()
        out = [{'day': d.day.isoformat(), 'o': d.open_price, 'h': d.high_price, 'l': d.low_price, 'c': d.close_price} for d in sums]
        return jsonify({'type':'daily','ticker':s.ticker,'data':out})
    else:
        ticks = StockPriceTick.query.filter_by(stock_id=s.stockId).order_by(StockPriceTick.timestamp.asc()).limit(limit).all()
        out = [{'ts': t.timestamp.isoformat(), 'p': t.price} for t in ticks]
        return jsonify({'type':'minute','ticker':s.ticker,'data':out})

@app.route('/compress_end_of_day', methods=['POST'])
@admin_required
def compress_end_of_day():
    day = request.form.get('day')
    if day:
        try:
            day_dt = datetime.strptime(day, "%Y-%m-%d").date()
        except:
            flash("Invalid date format, use YYYY-MM-DD", "danger")
            return redirect(url_for('admin_console'))
    else:
        day_dt = date.today() - timedelta(days=1)
    stocks = StockInventory.query.all()
    for s in stocks:
        compress_day_for_stock(s.stockId, day=day_dt)
    flash(f"Compressed data for {day_dt.isoformat()}. Minute-level ticks for that day were removed (summaries saved).", "success")
    return redirect(url_for('admin_console'))

def is_weekend(d: date):
    return d.weekday() >= 5

def is_holiday(d: date):
    return d in HARDCODED_HOLIDAYS
@app.route('/calendar', methods=['GET', 'POST'])
@login_required
def calendar():
    user = User.query.get(session.get('user_id'))
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('login'))

    if request.method == 'POST':
        flash("Use the admin panel to add events.", "danger")
        return redirect(url_for('calendar'))

    if user.role == 'admin':
        events = CalendarEvent.query.order_by(CalendarEvent.start_datetime.asc()).all()
    else:
        events = CalendarEvent.query.filter_by(created_by=user.id).order_by(CalendarEvent.start_datetime.asc()).all()

    events_json = [
        {
            "title": e.title,
            "start": e.start_datetime.isoformat(),
            "end": e.end_datetime.isoformat() if e.end_datetime else None
        } for e in events
    ]
    holidays = [d.isoformat() for d in HARDCODED_HOLIDAYS]
    return render_template('calendar.html', events_json=events_json, holidays=holidays)

@app.route('/calendar/add', methods=['POST'])
@admin_required
def calendar_add():
    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    start_date = request.form.get('start_date')
    start_time = request.form.get('start_time')
    end_date = request.form.get('end_date') or start_date
    end_time = request.form.get('end_time') or start_time
    event_type = request.form.get('event_type', 'event')
    custom_open_time = request.form.get('custom_open_time')
    custom_close_time = request.form.get('custom_close_time')

    if not title or not start_date or not start_time:
        flash("Title, start date, and start time are required.", "danger")
        return redirect(url_for('admin_console'))

    try:
        start_dt = datetime.strptime(f"{start_date} {start_time}", "%Y-%m-%d %H:%M")
        end_dt = datetime.strptime(f"{end_date} {end_time}", "%Y-%m-%d %H:%M")
        custom_open = datetime.strptime(custom_open_time, "%H:%M").time() if custom_open_time else None
        custom_close = datetime.strptime(custom_close_time, "%H:%M").time() if custom_close_time else None
    except ValueError:
        flash("Invalid date or time format.", "danger")
        return redirect(url_for('admin_console'))

    evt = CalendarEvent(
        title=title,
        description=description,
        start_datetime=start_dt,
        end_datetime=end_dt,
        created_by=session.get('user_id'),
        event_type=event_type,
        custom_open_time=custom_open,
        custom_close_time=custom_close
    )

    db.session.add(evt)
    db.session.commit()
    flash("Event added successfully.", "success")
    return redirect(url_for('admin_console'))


@app.route('/calendar/remove/<int:event_id>', methods=['POST'])
@admin_required
def calendar_remove(event_id):
    evt = CalendarEvent.query.get_or_404(event_id)
    db.session.delete(evt)
    db.session.commit()
    flash("Event removed successfully.", "success")
    return redirect(url_for('admin_console'))

@app.route('/user/<int:user_id>')
@admin_required
def admin_user_view(user_id):
    u = User.query.get_or_404(user_id)
    return render_template('admin_user.html', user=u)

@app.route('/delete_account', methods=['POST'])
@login_required
def delete_account():
    user = User.query.get(session['user_id'])
    if user:
        for p in list(user.portfolio):
            stock = p.stock
            if stock:
                stock.quantity += p.quantity
            db.session.delete(p)
        Order.query.filter_by(user_id=user.id).delete()
        db.session.delete(user)
        db.session.commit()
        session.clear()
        flash("Your account has been deleted.", "success")
    return redirect(url_for('home'))

@app.route('/delete_user/<int:user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    u = User.query.get_or_404(user_id)
    for p in list(u.portfolio):
        stock = p.stock
        if stock:
            stock.quantity += p.quantity
        db.session.delete(p)
    Order.query.filter_by(user_id=u.id).delete()
    db.session.delete(u)
    db.session.commit()
    flash("User deleted.", "success")
    return redirect(url_for('admin_console'))


@app.errorhandler(404)
def not_found(e):
    return render_template('404.html'), 404

if __name__ == '__main__':
    app.run(debug=True)
