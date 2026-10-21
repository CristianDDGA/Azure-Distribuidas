import os
import certifi
from flask import Flask, jsonify, request
##import pyodbc
import time
import threading
import pytds
import secrets
import requests
from html import escape

# =============================
# Conexion con la base de datos
# =============================

app = Flask(__name__)

def getConection():
    return pytds.connect(
        server=os.environ.get('DB_SERVER'),
        database=os.environ.get('DB_DATABASE'),
        user=os.environ.get('DB_USER'),
        password=os.environ.get('DB_PASSWORD'),
        port=1433,
        cafile=certifi.where(),
        validate_host=True
    )


# =============================
# Probar conexion
# =============================

@app.route('/test-db', methods=['GET'])
def test_db():
    try:
        conn = getConection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT DB_NAME()")
            result = cursor.fetchone()[0]
            cursor.close()

            return jsonify({
                "message": "Conexion exitosa a la base de datos",
                "result": result
            }), 200
        finally:
            conn.close()

    except Exception as e:
        return jsonify({
            "message": "Error al conectar a la base de datos",
            "error": str(e)
        }), 500


# =============================
# GET - Obtener productos
# =============================

@app.route('/api/products', methods=['GET'])
def get_products():
    try:
        conn = getConection()
        try:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT id, names, price, stock, version FROM products_vm"
            )

            products = []

            for row in cursor.fetchall():
                products.append({
                    "ID": row[0],
                    "NOMBRE": row[1],
                    "PRECIO": float(row[2]),
                    "STOCK": row[3],
                    "VERSION": row[4]
                })

            cursor.close()

            return jsonify({
                "message": "Productos obtenidos exitosamente",
                "result": products
            }), 200
        finally:
            conn.close()

    except Exception as e:
        return jsonify({
            "message": "Error al obtener productos",
            "error": str(e)
        }), 500

# ==========================================
# POST - Enviar catalogo mediante Brevo
# ==========================================

@app.route('/api/products/send-email', methods=['POST'])
def send_products_email():

    # Validar el token enviado por WordPress.
    token_configurado = os.environ.get('CATALOGO_API_TOKEN', '')

    if not token_configurado:
        return jsonify({
            "message": "Falta configurar CATALOGO_API_TOKEN en Render"
        }), 503

    autorizacion = request.headers.get('Authorization', '')
    token_recibido = autorizacion.removeprefix('Bearer ')

    if (
        not autorizacion.startswith('Bearer ')
        or not secrets.compare_digest(
            token_recibido.encode('utf-8'),
            token_configurado.encode('utf-8')
        )
    ):
        return jsonify({
            "message": "Token de acceso invalido"
        }), 401

    # Obtener configuracion desde Render.
    api_key = os.environ.get('BREVO_API_KEY')
    remitente = os.environ.get('BREVO_SENDER_EMAIL')
    nombre_remitente = os.environ.get(
        'BREVO_SENDER_NAME',
        'App-Distribuidas'
    )
    destinatario = os.environ.get('CATALOGO_DESTINO_EMAIL')

    if not all([api_key, remitente, destinatario]):
        return jsonify({
            "message": "Faltan variables de configuracion de Brevo en Render"
        }), 503

    # Reutilizar tu funcion existente.
    # Esta llamada es interna: no realiza otra peticion HTTP a Render.
    respuesta_productos, codigo = get_products()

    if codigo != 200:
        return jsonify({
            "message": "No se pudieron consultar los productos para el correo"
        }), 500

    productos = respuesta_productos.get_json().get('result', [])

    if not productos:
        return jsonify({
            "message": "No existen productos para enviar"
        }), 400

    try:
        filas = ''

        for producto in productos:
            nombre = escape(str(producto['NOMBRE']))
            precio = f"{float(producto['PRECIO']):,.2f}"
            cantidad = int(producto['STOCK'])

            filas += f"""
                <tr>
                    <td style="padding:12px;border:1px solid #ddd;">
                        {nombre}
                    </td>
                    <td style="padding:12px;border:1px solid #ddd;">
                        ${precio}
                    </td>
                    <td style="padding:12px;border:1px solid #ddd;">
                        {cantidad}
                    </td>
                </tr>
            """

        contenido = f"""
        <!DOCTYPE html>
        <html lang="es">
        <head>
            <meta charset="UTF-8">
        </head>
        <body style="font-family:Arial,sans-serif;color:#222;">
            <h1 style="color:#2563eb;">Catalogo de Productos</h1>

            <h2>Bienvenido, Cristian Ango</h2>

            <p>Estos son nuestros productos y sus cantidades disponibles.</p>

            <table style="width:100%;border-collapse:collapse;text-align:left;">
                <thead>
                    <tr style="background:#2563eb;color:white;">
                        <th style="padding:12px;">Producto</th>
                        <th style="padding:12px;">Precio</th>
                        <th style="padding:12px;">Cantidad</th>
                    </tr>
                </thead>
                <tbody>
                    {filas}
                </tbody>
            </table>

            <p>App-Distribuidas</p>
        </body>
        </html>
        """

        envio = requests.post(
            'https://api.brevo.com/v3/smtp/email',
            headers={
                'accept': 'application/json',
                'content-type': 'application/json',
                'api-key': api_key
            },
            json={
                'sender': {
                    'name': nombre_remitente,
                    'email': remitente
                },
                'to': [
                    {
                        'email': destinatario,
                        'name': 'Cristian Ango'
                    }
                ],
                'subject': 'Catalogo de productos - App-Distribuidas',
                'htmlContent': contenido
            },
            timeout=(5, 20)
        )

        if envio.status_code != 201:
            app.logger.warning(
                "Brevo rechazo el envio. HTTP %s",
                envio.status_code
            )
            return jsonify({
                "message": "Brevo no acepto el envio",
                "brevo_http": envio.status_code
            }), 502

        return jsonify({
            "message": "Brevo acepto el envio del catalogo"
        }), 200

    except requests.exceptions.Timeout:
        return jsonify({
            "message": (
                "Se agoto el tiempo de espera de Brevo. "
                "Revisa el correo o los registros antes de volver a enviar."
            )
        }), 504

    except requests.exceptions.RequestException:
        return jsonify({
            "message": "No se pudo confirmar el envio con Brevo"
        }), 502

    except Exception:
        app.logger.exception("Error al preparar el catalogo")
        return jsonify({
            "message": "Error al preparar el catalogo para enviar"
        }), 500




# =============================
# GET - Obtener producto por ID
# =============================

@app.route('/api/products/<int:ID>', methods=['GET'])
def get_product_by_id(ID):
    try:
        conn = getConection()
        try:
            cursor = conn.cursor()

            cursor.execute(
                """SELECT id, names, price, stock, version
                   FROM products_vm
                   WHERE id = ?""",
                (ID,)
            )

            row = cursor.fetchone()
            cursor.close()

            if row:
                product = {
                    "ID": row[0],
                    "NOMBRE": row[1],
                    "PRECIO": float(row[2]),
                    "STOCK": row[3],
                    "VERSION": row[4]
                }

                return jsonify({
                    "message": "Producto obtenido exitosamente",
                    "result": product
                }), 200

            return jsonify({
                "message": f"No se encontro un producto con ID {ID}"
            }), 404
        finally:
            conn.close()

    except Exception as e:
        return jsonify({
            "message": "Error al obtener el producto",
            "error": str(e)
        }), 500


# =============================
# POST - Crear producto
# =============================

@app.route('/api/products', methods=['POST'])
def create_product():
    conn = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "message": "No se proporcionaron datos validos"
            }), 400

        names = data.get('NOMBRE')
        price = data.get('PRECIO')
        stock = data.get('STOCK')

        if not names or price is None or stock is None:
            return jsonify({
                "message": "Faltan campos obligatorios"
            }), 400

        conn = getConection()
        cursor = conn.cursor()

        cursor.execute(
            """INSERT INTO products (names, price, stock)
               VALUES (?, ?, ?)""",
            (names, price, stock)
        )

        conn.commit()
        cursor.close()

        return jsonify({
            "message": "Producto creado exitosamente"
        }), 201

    except Exception as e:
        if conn:
            conn.rollback()

        return jsonify({
            "message": "Error al crear el producto",
            "error": str(e)
        }), 500

    finally:
        if conn:
            conn.close()


# ===================================
# PUT - Actualizacion sin controles
# ===================================

@app.route('/api/products', methods=['PUT'])
def update_products():
    conn = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "error": "Sin data"
            }), 400

        required = ['ID', 'NOMBRE', 'PRECIO', 'STOCK']

        if any(data.get(k) is None for k in required):
            return jsonify({
                "message": "Faltan campos obligatorios"
            }), 400

        conn = getConection()
        cursor = conn.cursor()

        # Simulacion de demora
        time.sleep(5)

        cursor.execute(
            """UPDATE products
               SET names = ?, price = ?, stock = ?
               WHERE id = ?""",
            (
                data['NOMBRE'],
                data['PRECIO'],
                data['STOCK'],
                data['ID']
            )
        )

        if cursor.rowcount == 0:
            conn.rollback()
            cursor.close()

            return jsonify({
                "message": "Producto no encontrado"
            }), 404

        conn.commit()
        cursor.close()

        return jsonify({
            "message": "Producto actualizado exitosamente"
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()

        return jsonify({
            "message": "Error al actualizar",
            "error": str(e)
        }), 500

    finally:
        if conn:
            conn.close()


# ===================================
# PUT - Actualizacion con Lock
# ===================================

product_lock = threading.Lock()

@app.route('/api/products/lock', methods=['PUT'])
def update_lock():
    conn = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "msg": "Falta informacion"
            }), 400

        required = ['ID', 'NOMBRE', 'PRECIO', 'STOCK']

        if any(data.get(k) is None for k in required):
            return jsonify({
                "msg": "Faltan campos obligatorios"
            }), 400

        with product_lock:
            print("Entra al bloqueo")

            try:
                conn = getConection()
                cursor = conn.cursor()

                cursor.execute(
                    """UPDATE products
                       SET names = ?, price = ?, stock = ?
                       WHERE id = ?""",
                    (
                        data['NOMBRE'],
                        data['PRECIO'],
                        data['STOCK'],
                        data['ID']
                    )
                )

                if cursor.rowcount == 0:
                    conn.rollback()
                    cursor.close()

                    return jsonify({
                        "msg": "Producto no encontrado"
                    }), 404

                conn.commit()
                cursor.close()

                print("Sale el bloqueo")

            finally:
                if conn:
                    conn.close()
                    conn = None

        return jsonify({
            "msg": "Producto actualizado exitosamente"
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()

        return jsonify({
            "message": "Error al actualizar",
            "error": str(e)
        }), 500


# ===================================
# PUT - Actualizacion con Version
# ===================================

@app.route('/api/products/version', methods=['PUT'])
def update_version():
    conn = None

    try:
        data = request.get_json(silent=True)

        if not isinstance(data, dict):
            return jsonify({
                "msg": "Falta informacion"
            }), 400

        required = [
            'ID', 'NOMBRE', 'PRECIO', 'STOCK', 'VERSION'
        ]

        if any(data.get(k) is None for k in required):
            return jsonify({
                "msg": "Faltan campos obligatorios"
            }), 400

        conn = getConection()
        cursor = conn.cursor()

        cursor.execute(
            """UPDATE products
               SET names = ?,
                   price = ?,
                   stock = ?,
                   version = version + 1
               WHERE id = ? AND version = ?""",
            (
                data['NOMBRE'],
                data['PRECIO'],
                data['STOCK'],
                data['ID'],
                data['VERSION']
            )
        )

        if cursor.rowcount == 0:
            conn.rollback()

            cursor.execute(
                "SELECT version FROM products WHERE id = ?",
                (data['ID'],)
            )

            row = cursor.fetchone()
            cursor.close()

            if row:
                return jsonify({
                    "msg": "Ya actualizaron este registro",
                    "error": {
                        "VERSION": row[0]
                    }
                }), 409

            return jsonify({
                "msg": "Producto no encontrado"
            }), 404

        conn.commit()
        cursor.close()

        return jsonify({
            "msg": "Producto actualizado exitosamente",
            "VERSION": data['VERSION'] + 1
        }), 200

    except Exception as e:
        if conn:
            conn.rollback()

        return jsonify({
            "message": "Error al actualizar",
            "error": str(e)
        }), 500

    finally:
        if conn:
            conn.close()


# =============================
# Iniciar servidor
# =============================

if __name__ == '__main__':
    app.run(
        host='0.0.0.0',
        port=5000,
        debug=True,
        threaded=True
    )
