from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import sqlite3
from typing import List, Optional
from datetime import datetime

DB_FILE = "precificador.db"

app = FastAPI(title="API Precificador de Doces")

# Permitir front-end
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Helpers ---
def get_conn():
    conn = sqlite3.connect(DB_FILE)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_conn()
    cur = conn.cursor()

    # Tabela de ingredientes
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

    # Tabela de receitas
    cur.execute("""
        CREATE TABLE IF NOT EXISTS receitas (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nome TEXT NOT NULL,
            categoria TEXT,
            rendimento REAL DEFAULT 1,
            embalagem REAL DEFAULT 0,
            margem REAL DEFAULT 50,
            custo_total REAL DEFAULT 0,
            preco_sugerido REAL DEFAULT 0,
            preco_por_unidade REAL DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # Tabela receita x ingrediente
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

# --- Models ---
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
    rendimento: float = 1.0
    embalagem: float = 0.0
    margem: float = 50.0
    ingredientes: List[ReceitaIngredienteIn] = []

# --- Rotas Ingredientes ---
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

# --- Rotas Receitas ---
@app.post("/receitas")
def create_receita(receita: ReceitaIn):
    conn = get_conn()
    cur = conn.cursor()

    try:
        # 1️⃣ Inserir receita básica
        cur.execute(
            "INSERT INTO receitas (nome, categoria, rendimento, embalagem, margem) VALUES (?, ?, ?, ?, ?)",
            (receita.nome, receita.categoria, receita.rendimento, receita.embalagem, receita.margem)
        )
        receita_id = cur.lastrowid

        # 2️⃣ Inserir ingredientes e calcular custo total
        custo_total = 0.0
        for item in receita.ingredientes:
            cur.execute(
                "SELECT amount, price, unit, density, nome FROM ingredientes WHERE id=?",
                (item.ingrediente_id,)
            )
            ing = cur.fetchone()
            if not ing:
                raise HTTPException(status_code=404, detail=f"Ingrediente {item.ingrediente_id} não encontrado")
            
            amount, price, unit, density, nome = ing

            # --- Conversão de unidades ---
            conv = item.quantidade
            if item.unidade != unit:
                if item.unidade == "ml" and unit == "g":
                    conv = item.quantidade * density
                elif item.unidade == "g" and unit == "ml":
                    conv = item.quantidade / density
                elif item.unidade == "unit" and unit != "unit":
                    conv = item.quantidade * amount
                elif unit == "unit" and item.unidade != "unit":
                    conv = item.quantidade / amount
                # Se conversão inválida, mantém a quantidade original

            custo_item = (conv / amount) * price
            custo_total += custo_item

            # Inserir relação receita x ingrediente
            cur.execute(
                "INSERT INTO receita_ingredientes (receita_id, ingrediente_id, quantidade, unidade) VALUES (?, ?, ?, ?)",
                (receita_id, item.ingrediente_id, item.quantidade, item.unidade)
            )

        # 3️⃣ Calcular valores finais
        total_com_embalagem = custo_total + receita.embalagem
        preco_sugerido = total_com_embalagem * (1 + receita.margem / 100)
        preco_por_unidade = preco_sugerido / max(receita.rendimento, 1)

        # 4️⃣ Atualizar receita com valores calculados
        cur.execute(
            "UPDATE receitas SET custo_total=?, preco_sugerido=?, preco_por_unidade=? WHERE id=?",
            (total_com_embalagem, preco_sugerido, preco_por_unidade, receita_id)
        )

        conn.commit()

    except Exception as e:
        conn.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao salvar receita: {str(e)}")
    finally:
        cur.close()
        conn.close()

    # Retornar receita completa
    return {
        "id": receita_id,
        "nome": receita.nome,
        "categoria": receita.categoria,
        "rendimento": receita.rendimento,
        "custo_total": round(total_com_embalagem, 2),
        "preco_sugerido": round(preco_sugerido, 2),
        "preco_por_unidade": round(preco_por_unidade, 2),
        "ingredientes": receita.ingredientes
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

# --- Inicializa banco ao iniciar ---
init_db()
