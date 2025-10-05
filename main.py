# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import mysql.connector
import os
from typing import List, Optional
from datetime import datetime

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "1234")
DB_NAME = os.getenv("DB_NAME", "precificador")
DB_PORT = int(os.getenv("DB_PORT", "3306"))

app = FastAPI(title="API Precificador de Doces")

# permitir frontend local
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

def get_conn():
    return mysql.connector.connect(
        host=DB_HOST,
        user=DB_USER,
        password=DB_PASS,
        database=DB_NAME,
        port=DB_PORT,
        autocommit=True
    )

# Pydantic models
class IngredienteIn(BaseModel):
    nome: str
    unit: str = "g"         # 'g','ml' ou 'unit'
    amount: float = 100.0   # quantidade base (ex: 100g)
    price: float = 0.0      # preço para 'amount'
    density: float = 1.0    # g/ml or g per unit

class IngredienteOut(IngredienteIn):
    id: int
    created_at: Optional[datetime] = None

class ReceitaIngredienteIn(BaseModel):
    ingrediente_id: int
    quantidade: float
    unidade: str = "g"

class ReceitaIn(BaseModel):
    nome: str
    categoria: Optional[str] = None
    embalagem: float = 0.0
    margem: float = 50.0
    ingredientes: List[ReceitaIngredienteIn] = []

class ReceitaOut(ReceitaIn):
    id: int
    custo_total: float
    preco_sugerido: float
    created_at: Optional[datetime] = None

# --- Ingredientes CRUD ---
@app.get("/ingredientes", response_model=List[IngredienteOut])
def list_ingredientes():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM ingredientes ORDER BY nome ASC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return rows

@app.post("/ingredientes", response_model=IngredienteOut)
def create_ingrediente(payload: IngredienteIn):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingredientes (nome, unit, amount, price, density) VALUES (%s,%s,%s,%s,%s)",
        (payload.nome, payload.unit, payload.amount, payload.price, payload.density)
    )
    new_id = cur.lastrowid
    conn.commit()
    cur.close()
    conn.close()
    return {**payload.dict(), "id": new_id}

@app.put("/ingredientes/{id}", response_model=IngredienteOut)
def update_ingrediente(id: int, payload: IngredienteIn):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "UPDATE ingredientes SET nome=%s, unit=%s, amount=%s, price=%s, density=%s WHERE id=%s",
        (payload.nome, payload.unit, payload.amount, payload.price, payload.density, id)
    )
    if cur.rowcount == 0:
        cur.close(); conn.close()
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado")
    conn.commit()
    cur.close()
    conn.close()
    return {**payload.dict(), "id": id}

@app.delete("/ingredientes/{id}")
def delete_ingrediente(id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("DELETE FROM ingredientes WHERE id=%s", (id,))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado")
    return {"deleted": id}

# --- Receitas ---
@app.post("/receitas", response_model=ReceitaOut)
def create_receita(payload: ReceitaIn):
    conn = get_conn()
    cur = conn.cursor()
    # calcular custo: pegamos preços atuais dos ingredientes
    total_ing_cost = 0.0
    for ri in payload.ingredientes:
        # obter ingrediente
        cur.execute("SELECT * FROM ingredientes WHERE id=%s", (ri.ingrediente_id,))
        ing = cur.fetchone()
        if not ing:
            cur.close(); conn.close()
            raise HTTPException(status_code=404, detail=f"Ingrediente {ri.ingrediente_id} não encontrado")
        # ing fields: (id,nome,unit,amount,price,density,created_at)
        unit_db = ing[2]
        amount_db = float(ing[3])
        price_db = float(ing[4])
        density_db = float(ing[5])
        # converter quantidade do payload para a unidade do catalog (unit_db)
        converted = convert_units_for_calc(ri.quantidade, ri.unidade, unit_db, density_db)
        if converted is None:
            cur.close(); conn.close()
            raise HTTPException(status_code=400, detail=f"Incompatível conversão para ingrediente {ing[1]}")
        # custo proporcional
        cost = (converted / amount_db) * price_db
        total_ing_cost += cost

    emb = float(payload.embalagem or 0)
    total = total_ing_cost + emb
    preco_sugerido = total * (1 + (float(payload.margem) or 0.0) / 100.0)

    # inserir receita
    cur.execute(
        "INSERT INTO receitas (nome, categoria, embalagem, margem, custo_total, preco_sugerido) VALUES (%s,%s,%s,%s,%s,%s)",
        (payload.nome, payload.categoria, emb, payload.margem, round(total,4), round(preco_sugerido,4))
    )
    receita_id = cur.lastrowid

    # inserir ingredientes da receita
    for ri in payload.ingredientes:
        cur.execute(
            "INSERT INTO receita_ingredientes (receita_id, ingrediente_id, quantidade, unidade) VALUES (%s,%s,%s,%s)",
            (receita_id, ri.ingrediente_id, ri.quantidade, ri.unidade)
        )
    conn.commit()
    cur.close()
    conn.close()

    return {
        "id": receita_id,
        "nome": payload.nome,
        "categoria": payload.categoria,
        "embalagem": emb,
        "margem": payload.margem,
        "ingredientes": payload.ingredientes,
        "custo_total": round(total,4),
        "preco_sugerido": round(preco_sugerido,4)
    }

@app.get("/receitas")
def list_receitas():
    conn = get_conn()
    cur = conn.cursor(dictionary=True)
    cur.execute("SELECT * FROM receitas ORDER BY created_at DESC")
    receitas = cur.fetchall()
    # opcional: trazer ingredientes por receita
    for r in receitas:
        cur2 = conn.cursor(dictionary=True)
        cur2.execute("""
            SELECT ri.id, ri.quantidade, ri.unidade, i.id AS ingrediente_id, i.nome, i.unit AS ingrediente_unit, i.price, i.amount, i.density
            FROM receita_ingredientes ri
            JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE ri.receita_id=%s
        """, (r["id"],))
        r["itens"] = cur2.fetchall()
        cur2.close()
    cur.close()
    conn.close()
    return receitas

# --- helper conversion ---
def convert_units_for_calc(value, from_unit, to_unit, density):
    """
    Returns numeric converted value in 'to_unit' units.
    If impossible, returns None.
    density: g per ml or g per unidade (when converting unit)
    """
    v = float(value or 0)
    if from_unit == to_unit:
        return v
    if from_unit == "unit" or to_unit == "unit":
        # allow conversion if density is provided (grams per unit)
        if from_unit == "unit" and to_unit != "unit":
            # unit -> grams -> maybe ml
            grams = v * density
            if to_unit == "g": return grams
            if to_unit == "ml": return grams / (density or 1)
        if to_unit == "unit" and from_unit != "unit":
            # grams or ml -> units
            if from_unit == "g":
                grams = v
            elif from_unit == "ml":
                grams = v * density
            else:
                return None
            # units = grams / density
            if density == 0: return None
            return grams / density
        return None
    # ml <-> g using density (g/ml)
    if from_unit == "ml" and to_unit == "g":
        return v * (density or 1)
    if from_unit == "g" and to_unit == "ml":
        if (density or 1) == 0: return None
        return v / (density or 1)
    return None
