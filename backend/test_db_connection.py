from sqlalchemy import text

from app.database.connection import engine

try:
    with engine.connect() as connection:
        result = connection.execute(text("SELECT current_database(), version()"))

        database, version = result.fetchone()

        print("Database connected successfully!")
        print("Database:", database)
        print("Version:", version)

except Exception as error:
    print("Database connection failed:")
    print(error)

finally:
    engine.dispose()
