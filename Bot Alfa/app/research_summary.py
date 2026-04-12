import sqlite3
import pandas as pd
import os
from datetime import datetime

DB_PATH = "data/market_analysis.db"

def generate_weekly_research_report():
    if not os.path.exists(DB_PATH):
        print("❌ No se encontró la base de datos de investigación.")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        
        # 1. Cargar datos de investigación
        query = "SELECT * FROM opening_research"
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            print("⚠️ La tabla de investigación está vacía. Esperando a la próxima apertura (9:30 AM).")
            return

        # Asegurar orden de los días
        dias_orden = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday']
        df['day_of_week'] = pd.Categorical(df['day_of_week'], categories=dias_orden, ordered=True)

        print("\n" + "="*60)
        print("📊 RESUMEN DE INVESTIGACIÓN: PRECIOS DE APERTURA (9:30 AM)")
        print("="*60)

        for ticker in df['ticker'].unique():
            ticker_df = df[df['ticker'] == ticker]
            
            print(f"\n--- ACTIVO: {ticker} ---")
            
            # Pivotar para ver: Filas = Día, Columnas = Distancia (Offset), Valores = Precio Mid
            summary = ticker_df.pivot_table(
                index='day_of_week', 
                columns='strike_offset', 
                values='mid', 
                aggfunc='mean'
            )
            
            # Renombrar columnas para claridad
            summary.columns = [f"Strike {int(c):+d}" if c != 0 else "ATM (0)" for c in summary.columns]
            
            print(summary.round(2).to_string())
            
            # Calcular volatilidad promedio del spread
            avg_spread = ticker_df.groupby('day_of_week')['spread_pct'].mean()
            print(f"\nSpread Promedio (%):")
            print(avg_spread.round(2).to_string())
            print("-" * 30)

        # Guardar a CSV para que el usuario pueda abrirlo en Excel
        report_name = f"analysis/research_summary_{datetime.now().strftime('%Y%m%d')}.csv"
        os.makedirs("analysis", exist_ok=True)
        df.to_csv(report_name, index=False)
        print(f"\n✅ Informe detallado guardado en: {report_name}")

    except Exception as e:
        print(f"❌ Error generando el resumen: {e}")

if __name__ == "__main__":
    generate_weekly_research_report()
