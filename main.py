# main.py
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from typing import List, Optional
from datetime import datetime

DB_FILE = "precificador.db"

app = FastAPI(title="API Precificador de Doces")

# permitir frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- helpers ---
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()
    # tabela de ingredientes
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            unit TEXT DEFAULT 'g',
            amount REAL DEFAULT 100,
            price REAL DEFAULT 0,
            density REAL DEFAULT 1,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # tabela de receitas
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT,
            embalagem REAL DEFAULT 0,
            margem REAL DEFAULT 50,
            custo_total REAL DEFAULT 0,
            preco_sugerido REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    # tabela relação receita x ingrediente
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receita_ingredientes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            receita_id INTEGER NOT NULL,
            ingrediente_id INTEGER NOT NULL,
            quantidade REAL DEFAULT 0,
            unidade TEXT DEFAULT 'g',
            FOREIGN KEY(receita_id) REFERENCES receitas(id),
            FOREIGN KEY(ingrediente_id) REFERENCES ingredientes(id)
        )
    """)
    conn.commit()
    cur.close()
    conn.close()

# --- models ---
class IngredienteIn(BaseModel):
    nome: str
    unit: str = "g"
    amount: float = 100.0
    price: float = 0.0
    density: float = 1.0

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

# --- routes ingredientes ---
@app.get("/ingredientes", response_model=List[IngredienteOut])
def list_ingredientes():
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM ingredientes ORDER BY nome ASC")
    rows = [dict(r) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows

@app.post("/ingredientes", response_model=IngredienteOut)
def create_ingrediente(payload: IngredienteIn):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO ingredientes (nome, unit, amount, price, density) VALUES (?,?,?,?,?)",
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
        "UPDATE ingredientes SET nome=?, unit=?, amount=?, price=?, density=? WHERE id=?",
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
    cur.execute("DELETE FROM ingredientes WHERE id=?", (id,))
    affected = cur.rowcount
    conn.commit()
    cur.close()
    conn.close()
    if affected == 0:
        raise HTTPException(status_code=404, detail="Ingrediente não encontrado")
    return {"deleted": id}

# --- routes receitas ---
@app.post("/receitas", response_model=ReceitaOut)
def create_receita(payload: ReceitaIn):
    conn = get_conn()
    cur = conn.cursor()
    total_ing_cost = 0.0
    for ri in payload.ingredientes:
        cur.execute("SELECT * FROM ingredientes WHERE id=?", (ri.ingrediente_id,))
        ing = cur.fetchone()
        if not ing:
            cur.close(); conn.close()
            raise HTTPException(status_code=404, detail=f"Ingrediente {ri.ingrediente_id} não encontrado")
        unit_db = ing["unit"]
        amount_db = float(ing["amount"])
        price_db = float(ing["price"])
        density_db = float(ing["density"])
        converted = convert_units_for_calc(ri.quantidade, ri.unidade, unit_db, density_db)
        if converted is None:
            cur.close(); conn.close()
            raise HTTPException(status_code=400, detail=f"Incompatível conversão para ingrediente {ing['nome']}")
        cost = (converted / amount_db) * price_db
        total_ing_cost += cost

    emb = float(payload.embalagem or 0)
    total = total_ing_cost + emb
    preco_sugerido = total * (1 + (float(payload.margem) or 0) / 100.0)

    cur.execute(
        "INSERT INTO receitas (nome, categoria, embalagem, margem, custo_total, preco_sugerido) VALUES (?,?,?,?,?,?)",
        (payload.nome, payload.categoria, emb, payload.margem, round(total,4), round(preco_sugerido,4))
    )
    receita_id = cur.lastrowid
    for ri in payload.ingredientes:
        cur.execute(
            "INSERT INTO receita_ingredientes (receita_id, ingrediente_id, quantidade, unidade) VALUES (?,?,?,?)",
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
    cur = conn.cursor()
    cur.execute("SELECT * FROM receitas ORDER BY created_at DESC")
    rows = [dict(r) for r in cur.fetchall()]
    for r in rows:
        cur2 = conn.cursor()
        cur2.execute("""
            SELECT ri.id, ri.quantidade, ri.unidade, i.id AS ingrediente_id, i.nome, i.unit AS ingrediente_unit, i.price, i.amount, i.density
            FROM receita_ingredientes ri
            JOIN ingredientes i ON i.id = ri.ingrediente_id
            WHERE ri.receita_id=?
        """, (r["id"],))
        r["itens"] = [dict(i) for i in cur2.fetchall()]
        cur2.close()
    cur.close()
    conn.close()
    return rows

# --- conversão ---
def convert_units_for_calc(value, from_unit, to_unit, density):
    """
    Converte entre gramas (g), mililitros (ml) e unidade (unit)
    usando densidade para equivalência.
    Exemplo: 1 unidade de chocolate pesa 15g (density=15)
    """
    v = float(value or 0)
    d = float(density or 1)

    # Se for a mesma unidade, retorna igual
    if from_unit == to_unit:
        return v

    # === Conversões com 'unit' ===
    if from_unit == "unit" and to_unit == "g":
        return v * d  # unidade → gramas
    if from_unit == "unit" and to_unit == "ml":
        return v * d / d  # unidade → ml (equivalente à massa convertida pelo mesmo d)
    if to_unit == "unit" and from_unit == "g":
        return v / d  # gramas → unidade
    if to_unit == "unit" and from_unit == "ml":
        return (v * d) / d  # ml → unidade (via densidade)
    
    # === Conversões entre g e ml ===
    if from_unit == "g" and to_unit == "ml":
        return v / d
    if from_unit == "ml" and to_unit == "g":
        return v * d

    return None


# --- init db ao iniciar ---
init_db()
