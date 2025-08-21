import json
from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import os
import firebase_admin
from firebase_admin import credentials, firestore
import json # لإمكانية تحليل JSON من متغيرات البيئة

app = Flask(__name__, static_folder='.', static_url_path='')

# تهيئة CORS للسماح بالطلبات من أي مصدر (*).
# في بيئة الإنتاج، يفضل تحديد أصول محددة (domains) بدلاً من '*' لأسباب أمنية.
# مثال: CORS(app, resources={r"/*": {"origins": ["https://your-frontend-domain.com", "http://127.0.0.1:5000"]}})
CORS(app)

# تهيئة Firebase Admin SDK
# بيانات الاعتماد (credentials) يجب أن تأتي من متغير بيئة (Environment Variable)
# يسمى FIREBASE_SERVICE_ACCOUNT_KEY على Railway، ويحتوي على ملف JSON الخاص بحساب الخدمة.
firebase_credentials_json = os.environ.get('FIREBASE_SERVICE_ACCOUNT_KEY')

db = None # تهيئة db كـ None مبدئياً

if firebase_credentials_json:
    try:
        # تحميل بيانات الاعتماد من المتغير البيئي
        cred_data = json.loads(firebase_credentials_json)
        cred = credentials.Certificate(cred_data)
        
        # التأكد من أن Firebase لم يتم تهيئته مسبقاً (لتجنب الأخطاء في وضع التطوير)
        if not firebase_admin._apps:
            firebase_admin.initialize_app(cred)
        db = firestore.client()
        print("Firebase initialized successfully and Firestore client obtained!")
    except Exception as e:
        print(f"Error initializing Firebase: {e}")
else:
    print("FIREBASE_SERVICE_ACCOUNT_KEY environment variable not found. Firebase will NOT be initialized.")
    print("Please set this variable on Railway with your Firebase Service Account JSON content.")

# دالة مساعدة للتحقق من اتصال قاعدة البيانات
def check_db_connection():
    if db is None:
        return jsonify({"success": False, "message": "خطأ في اتصال قاعدة البيانات. يرجى مراجعة إعدادات الخادم."}), 500
    return None

# مسار الصفحة الرئيسية (صفحة تسجيل الدخول)
@app.route('/')
def serve_login_page():
    return send_from_directory('.', 'index.html')

# مسار لوحة التحكم
@app.route('/dashboard.html')
def serve_dashboard_page():
    return send_from_directory('.', 'dashboard.html')

# نقطة نهاية لتسجيل الدخول (POST request)
@app.route('/login', methods=['POST'])
def login():
    error_response = check_db_connection()
    if error_response:
        return error_response

    credentials_data = request.json
    username = credentials_data.get('username')
    password = credentials_data.get('password')

    try:
        users_ref = db.collection('users')
        # البحث عن المستخدم باسم المستخدم وكلمة المرور
        query = users_ref.where('username', '==', username).where('password', '==', password)
        docs = query.stream()

        for doc in docs:
            # إذا تم العثور على مستخدم يطابق، يتم تسجيل الدخول بنجاح
            return jsonify({"success": True, "message": "تم تسجيل الدخول بنجاح!"})
        
        return jsonify({"success": False, "message": "اسم المستخدم أو كلمة المرور غير صحيحة."}), 401
    except Exception as e:
        print(f"Error during login: {e}")
        return jsonify({"success": False, "message": "خطأ داخلي في الخادم أثناء تسجيل الدخول."}), 500

# نقطة نهاية لجلب جميع المنتجات (GET request)
@app.route('/products', methods=['GET'])
def get_products():
    error_response = check_db_connection()
    if error_response:
        return error_response

    try:
        products_ref = db.collection('products')
        docs = products_ref.stream()
        products_list = []
        for doc in docs:
            product_data = doc.to_dict()
            product_data['productId'] = doc.id # Firestore يستخدم doc.id كمعرف للمستند
            products_list.append(product_data)
        return jsonify(products_list)
    except Exception as e:
        print(f"Error fetching products: {e}")
        return jsonify({"success": False, "message": "خطأ داخلي في الخادم أثناء جلب المنتجات."}), 500

# نقطة نهاية لجلب منتج معين بواسطة تسلسله (GET request)
@app.route('/product/<product_id>', methods=['GET'])
def get_product(product_id):
    error_response = check_db_connection()
    if error_response:
        return error_response

    try:
        # البحث عن المستند باستخدام productId كـ doc.id
        product_ref = db.collection('products').document(product_id)
        doc = product_ref.get()

        if doc.exists:
            product_data = doc.to_dict()
            product_data['productId'] = doc.id # إضافة productId لبيانات المنتج
            return jsonify(product_data)
        return jsonify({"message": "المنتج غير موجود."}), 404
    except Exception as e:
        print(f"Error fetching product {product_id}: {e}")
        return jsonify({"success": False, "message": "خطأ داخلي في الخادم أثناء جلب المنتج."}), 500

# نقطة نهاية لتحديث كمية منتج (إضافة أو خصم) (POST request)
@app.route('/update_quantity', methods=['POST'])
def update_quantity():
    error_response = check_db_connection()
    if error_response:
        return error_response

    update_data = request.json
    product_id = update_data.get('productId')
    quantity_change = update_data.get('quantityChange')

    if not product_id or not isinstance(quantity_change, (int, float)):
        return jsonify({"success": False, "message": "بيانات غير صالحة للمنتج أو الكمية."}), 400

    try:
        product_ref = db.collection('products').document(product_id)
        # استخدام Firestore Transaction لضمان التحديث الذري (Atomic Update)
        # هذا يمنع مشاكل التزامن إذا قام عدة مستخدمين بتحديث نفس المنتج في نفس الوقت
        @firestore.transactional
        def update_product_quantity_transaction(transaction, ref, change):
            snapshot = ref.get(transaction=transaction)
            if not snapshot.exists:
                raise ValueError("المنتج غير موجود في قاعدة البيانات.")
            
            current_quantity = snapshot.get('quantity')
            new_quantity = current_quantity + change
            if new_quantity < 0:
                new_quantity = 0 # منع الكمية السالبة

            transaction.update(ref, {'quantity': new_quantity})
            return True

        transaction = db.transaction()
        update_product_quantity_transaction(transaction, product_ref, quantity_change)
        
        return jsonify({"success": True, "message": "تم تحديث الكمية بنجاح!"})
    except ValueError as ve:
        return jsonify({"success": False, "message": str(ve)}), 404
    except Exception as e:
        print(f"Error updating quantity for {product_id}: {e}")
        return jsonify({"success": False, "message": "خطأ داخلي في الخادم أثناء تحديث الكمية."}), 500

# نقطة نهاية لإضافة منتج جديد (POST request)
@app.route('/add_product', methods=['POST'])
def add_product():
    error_response = check_db_connection()
    if error_response:
        return error_response

    new_product_data = request.json
    company_name = new_product_data.get('companyName')
    product_id = new_product_data.get('productId')
    quantity = new_product_data.get('quantity')
    image_url = new_product_data.get('imageUrl')

    if not all([company_name, product_id, quantity is not None, image_url]):
        return jsonify({"success": False, "message": "يرجى إدخال جميع الحقول المطلوبة."}), 400

    try:
        product_ref = db.collection('products').document(product_id)
        doc = product_ref.get()

        if doc.exists:
            return jsonify({"success": False, "message": "منتج بهذا التسلسل موجود بالفعل!"}), 409
        
        # إضافة المنتج الجديد إلى Firestore، باستخدام productId كـ doc.id
        product_ref.set({
            "companyName": company_name,
            "quantity": quantity,
            "imageUrl": image_url
        })
        return jsonify({"success": True, "message": "تمت إضافة المنتج بنجاح!"})
    except Exception as e:
        print(f"Error adding product: {e}")
        return jsonify({"success": False, "message": "خطأ داخلي في الخادم أثناء إضافة المنتج."}), 500

if __name__ == '__main__':
    # تشغيل الخادم على المنفذ 5000 في وضع التطوير
    app.run(debug=True, port=5000)

