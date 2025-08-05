# EJERCICIOS REALIZADOS POR MICHAEL FELIPE CORRALES FLOREZ

import array
import os

def limpiar_pantalla():
    """Limpia la pantalla de la consola"""
    os.system('cls' if os.name == 'nt' else 'clear')

def pausa():
    """Pausa la ejecución hasta que el usuario presione Enter"""
    input("\nPresiona Enter para continuar...")

def mostrar_menu():
    """Muestra el menú principal"""
    print("="*60)
    print("    ESTRUCTURAS DE DATOS EN PYTHON - MENÚ INTERACTIVO")
    print("="*60)
    print("1.  Ejemplos de Listas")
    print("2.  Ejemplos de Tuplas")
    print("3.  Ejemplos de Arreglos")
    print("4.  Ejemplos de Diccionarios")
    print("5.  Lista de Diccionarios")
    print("6.  Diccionarios Anidados")
    print("7.  Ejercicio 1: Crear y recorrer una lista")
    print("8.  Ejercicio 2: Modificar una lista")
    print("9.  Ejercicio 3: Buscar elementos en una lista")
    print("10. Ejercicio 4: Crear un diccionario simple")
    print("11. Ejercicio 5: Modificar y agregar claves")
    print("12. Ejercicio 6: Recorrer un diccionario")
    print("13. Ejercicio 7: Lista de diccionarios")
    print("14. Ejercicio 8: Buscar en lista de diccionarios")
    print("15. Ejercicio 9: Diccionario con listas como valores")
    print("16. Ejercicio 10: Diccionario anidado")
    print("0.  Salir")
    print("="*60)

def ejemplo_listas():
    """Ejemplos de listas en Python"""
    print("\n--- EJEMPLOS DE LISTAS ---")
    
    # Crear una lista
    mi_lista = [1, "hola", True, 3.14, "mundo"]
    print(f"Lista creada: {mi_lista}")
    print(f"Tipo de dato: {type(mi_lista)}")
    
    # Acceder a elementos por índice
    print(f"\nAcceso por índice:")
    print(f"Primer elemento (índice 0): {mi_lista[0]}")
    print(f"Segundo elemento (índice 1): {mi_lista[1]}")
    print(f"Último elemento (índice -1): {mi_lista[-1]}")
    
    # Modificar elementos
    print(f"\nLista original: {mi_lista}")
    mi_lista[1] = "python"
    print(f"Después de modificar índice 1: {mi_lista}")
    
    # Agregar elementos
    mi_lista.append("nuevo")
    print(f"Después de agregar elemento: {mi_lista}")
    
    # Eliminar elementos
    mi_lista.remove("mundo")
    print(f"Después de eliminar 'mundo': {mi_lista}")
    
    # Longitud de la lista
    print(f"\nLongitud de la lista: {len(mi_lista)}")
    
    # Recorrer la lista
    print("\nRecorriendo la lista:")
    for i, elemento in enumerate(mi_lista):
        print(f"  Índice {i}: {elemento}")

def ejemplo_tuplas():
    """Ejemplos de tuplas en Python"""
    print("\n--- EJEMPLOS DE TUPLAS ---")
    
    # Crear una tupla
    mi_tupla = (1, "hola", True, 3.14, "mundo")
    print(f"Tupla creada: {mi_tupla}")
    print(f"Tipo de dato: {type(mi_tupla)}")
    
    # Acceder a elementos por índice
    print(f"\nAcceso por índice:")
    print(f"Primer elemento (índice 0): {mi_tupla[0]}")
    print(f"Segundo elemento (índice 1): {mi_tupla[1]}")
    print(f"Último elemento (índice -1): {mi_tupla[-1]}")
    
    # Las tuplas son inmutables
    print(f"\nLas tuplas son INMUTABLES:")
    print("No se pueden modificar, agregar o eliminar elementos")
    
    try:
        mi_tupla[1] = "python"  # Esto causará un error
    except TypeError as e:
        print(f"Error al intentar modificar: {e}")
    
    # Longitud de la tupla
    print(f"\nLongitud de la tupla: {len(mi_tupla)}")
    
    # Recorrer la tupla
    print("\nRecorriendo la tupla:")
    for i, elemento in enumerate(mi_tupla):
        print(f"  Índice {i}: {elemento}")
    
    # Desempaquetado de tuplas
    print("\nDesempaquetado de tuplas:")
    coordenadas = (10, 20)
    x, y = coordenadas
    print(f"Coordenadas: {coordenadas}")
    print(f"x = {x}, y = {y}")

def ejemplo_arreglos():
    """Ejemplos de arreglos en Python"""
    print("\n--- EJEMPLOS DE ARREGLOS ---")
    
    # Crear un arreglo de enteros
    mi_arreglo = array.array('i', [1, 2, 3, 4, 5])
    print(f"Arreglo de enteros creado: {mi_arreglo}")
    print(f"Tipo de dato: {type(mi_arreglo)}")
    print(f"Código de tipo: 'i' = enteros")
    
    # Diferentes tipos de arreglos
    arreglo_float = array.array('f', [1.1, 2.2, 3.3])
    print(f"Arreglo de flotantes: {arreglo_float}")
    
    # Acceder a elementos
    print(f"\nAcceso por índice:")
    print(f"Primer elemento: {mi_arreglo[0]}")
    print(f"Último elemento: {mi_arreglo[-1]}")
    
    # Modificar elementos
    print(f"\nArreglo original: {mi_arreglo}")
    mi_arreglo[1] = 10
    print(f"Después de modificar índice 1: {mi_arreglo}")
    
    # Agregar elementos
    mi_arreglo.append(6)
    print(f"Después de agregar elemento: {mi_arreglo}")
    
    # Códigos de tipo comunes
    print(f"\nCódigos de tipo comunes:")
    print("'i' = enteros, 'f' = flotantes, 'd' = dobles, 'b' = bytes")
    
    # Ventajas de los arreglos
    print(f"\nVentajas: Más eficientes en memoria para datos del mismo tipo")
    print(f"Longitud del arreglo: {len(mi_arreglo)}")

def ejemplo_diccionarios():
    """Ejemplos de diccionarios en Python"""
    print("\n--- EJEMPLOS DE DICCIONARIOS ---")
    
    # Crear un diccionario
    persona = {
        "nombre": "Ana",
        "edad": 30,
        "ciudad": "Bogotá",
        "profesion": "Ingeniera"
    }
    print(f"Diccionario creado: {persona}")
    print(f"Tipo de dato: {type(persona)}")
    
    # Acceder a valores por clave
    print(f"\nAcceso por clave:")
    print(f"Nombre: {persona['nombre']}")
    print(f"Edad: {persona['edad']}")
    print(f"Ciudad: {persona.get('ciudad')}")  # Método alternativo
    
    # Modificar valores
    print(f"\nDiccionario original: {persona}")
    persona["edad"] = 31
    print(f"Después de cambiar edad: {persona}")
    
    # Agregar nuevas claves
    persona["telefono"] = "123-456-7890"
    print(f"Después de agregar teléfono: {persona}")
    
    # Eliminar claves
    del persona["telefono"]
    print(f"Después de eliminar teléfono: {persona}")
    
    # Métodos útiles
    print(f"\nMétodos útiles:")
    print(f"Claves: {list(persona.keys())}")
    print(f"Valores: {list(persona.values())}")
    print(f"Pares clave-valor: {list(persona.items())}")
    
    # Recorrer diccionario
    print(f"\nRecorriendo el diccionario:")
    for clave, valor in persona.items():
        print(f"  {clave}: {valor}")

def ejemplo_lista_diccionarios():
    """Ejemplos de listas de diccionarios"""
    print("\n--- LISTA DE DICCIONARIOS ---")
    
    # Crear lista de estudiantes
    estudiantes = [
        {"nombre": "Juan", "edad": 20, "promedio": 8.5},
        {"nombre": "María", "edad": 19, "promedio": 9.2},
        {"nombre": "Carlos", "edad": 21, "promedio": 7.8},
        {"nombre": "Ana", "edad": 20, "promedio": 9.0}
    ]
    
    print("Lista de estudiantes:")
    for i, estudiante in enumerate(estudiantes):
        print(f"  Estudiante {i+1}: {estudiante}")
    
    # Acceder a un estudiante específico
    print(f"\nPrimer estudiante: {estudiantes[0]}")
    print(f"Nombre del primer estudiante: {estudiantes[0]['nombre']}")
    
    # Recorrer y mostrar información
    print(f"\nInformación de todos los estudiantes:")
    for estudiante in estudiantes:
        print(f"  {estudiante['nombre']} - Edad: {estudiante['edad']} - Promedio: {estudiante['promedio']}")
    
    # Filtrar estudiantes
    print(f"\nEstudiantes con promedio >= 9.0:")
    for estudiante in estudiantes:
        if estudiante['promedio'] >= 9.0:
            print(f"  {estudiante['nombre']}: {estudiante['promedio']}")
    
    # Agregar nuevo estudiante
    nuevo_estudiante = {"nombre": "Luis", "edad": 22, "promedio": 8.7}
    estudiantes.append(nuevo_estudiante)
    print(f"\nDespués de agregar nuevo estudiante:")
    print(f"Total de estudiantes: {len(estudiantes)}")

def ejemplo_diccionarios_anidados():
    """Ejemplos de diccionarios anidados"""
    print("\n--- DICCIONARIOS ANIDADOS ---")
    
    # Crear diccionario anidado
    empresa = {
        "nombre": "TechCorp",
        "fundacion": 2010,
        "empleados": {
            "desarrollo": {
                "juan": {"edad": 28, "salario": 5000},
                "maria": {"edad": 25, "salario": 4500}
            },
            "marketing": {
                "carlos": {"edad": 30, "salario": 4000},
                "ana": {"edad": 27, "salario": 4200}
            }
        },
        "ubicaciones": {
            "sede_principal": {
                "ciudad": "Bogotá",
                "direccion": "Calle 123 #45-67"
            },
            "sucursal": {
                "ciudad": "Medellín",
                "direccion": "Carrera 10 #20-30"
            }
        }
    }
    
    print(f"Información de la empresa:")
    print(f"Nombre: {empresa['nombre']}")
    print(f"Año de fundación: {empresa['fundacion']}")
    
    print(f"\nAcceso a datos anidados:")
    print(f"Salario de Juan: {empresa['empleados']['desarrollo']['juan']['salario']}")
    print(f"Ciudad de sede principal: {empresa['ubicaciones']['sede_principal']['ciudad']}")
    
    print(f"\nEmpleados del departamento de desarrollo:")
    for nombre, info in empresa['empleados']['desarrollo'].items():
        print(f"  {nombre.capitalize()}: {info['edad']} años, ${info['salario']}")
    
    print(f"\nTodas las ubicaciones:")
    for tipo, info in empresa['ubicaciones'].items():
        print(f"  {tipo.replace('_', ' ').title()}: {info['ciudad']} - {info['direccion']}")

# Ejercicios
def ejercicio_1():
    """Ejercicio 1: Crear y recorrer una lista"""
    print("\n--- EJERCICIO 1: CREAR Y RECORRER UNA LISTA ---")
    
    estudiantes = ["Juan", "María", "Carlos", "Ana", "Luis"]
    print(f"Lista de estudiantes: {estudiantes}")
    
    print("\nNombres en mayúsculas:")
    for estudiante in estudiantes:
        print(f"  {estudiante.upper()}")

def ejercicio_2():
    """Ejercicio 2: Modificar una lista"""
    print("\n--- EJERCICIO 2: MODIFICAR UNA LISTA ---")
    
    numeros = [10, 20, 30, 40]
    print(f"Lista original: {numeros}")
    
    # Cambiar el segundo número
    numeros[1] = 25
    print(f"Después de cambiar el segundo número: {numeros}")
    
    # Agregar un número al final
    numeros.append(50)
    print(f"Después de agregar un número: {numeros}")
    
    # Eliminar el primero
    numeros.pop(0)
    print(f"Después de eliminar el primero: {numeros}")

def ejercicio_3():
    """Ejercicio 3: Buscar elementos en una lista"""
    print("\n--- EJERCICIO 3: BUSCAR ELEMENTOS EN UNA LISTA ---")
    
    frutas = ["manzana", "banana", "naranja", "uva", "pera"]
    print(f"Frutas disponibles: {frutas}")
    
    fruta_buscar = input("\n¿Qué fruta buscas? ").lower()
    
    if fruta_buscar in frutas:
        print(f"¡Sí! La fruta '{fruta_buscar}' está en la lista.")
    else:
        print(f"No, la fruta '{fruta_buscar}' no está en la lista.")

def ejercicio_4():
    """Ejercicio 4: Crear un diccionario simple"""
    print("\n--- EJERCICIO 4: CREAR UN DICCIONARIO SIMPLE ---")
    
    persona = {
        "nombre": "Pedro",
        "edad": 25,
        "ciudad": "Medellín"
    }
    
    print("Información de la persona:")
    print(f"Nombre: {persona['nombre']}")
    print(f"Edad: {persona['edad']}")
    print(f"Ciudad: {persona['ciudad']}")

def ejercicio_5():
    """Ejercicio 5: Modificar y agregar claves"""
    print("\n--- EJERCICIO 5: MODIFICAR Y AGREGAR CLAVES ---")
    
    persona = {
        "nombre": "Pedro",
        "edad": 25,
        "ciudad": "Medellín"
    }
    
    print(f"Diccionario original: {persona}")
    
    # Cambiar la ciudad
    persona["ciudad"] = "Cali"
    print(f"Después de cambiar ciudad: {persona}")
    
    # Agregar profesión
    persona["profesion"] = "Programador"
    print(f"Después de agregar profesión: {persona}")
    
    # Eliminar edad
    del persona["edad"]
    print(f"Después de eliminar edad: {persona}")

def ejercicio_6():
    """Ejercicio 6: Recorrer un diccionario"""
    print("\n--- EJERCICIO 6: RECORRER UN DICCIONARIO ---")
    
    persona = {
        "nombre": "Pedro",
        "ciudad": "Cali",
        "profesion": "Programador"
    }
    
    print("Recorriendo el diccionario:")
    for clave, valor in persona.items():
        print(f"{clave}: {valor}")

def ejercicio_7():
    """Ejercicio 7: Lista de diccionarios"""
    print("\n--- EJERCICIO 7: LISTA DE DICCIONARIOS ---")
    
    estudiantes = [
        {"nombre": "Juan", "edad": 20, "curso": "Python"},
        {"nombre": "María", "edad": 19, "curso": "JavaScript"},
        {"nombre": "Carlos", "edad": 21, "curso": "Python"}
    ]
    
    print("Nombres de los estudiantes:")
    for estudiante in estudiantes:
        print(f"  {estudiante['nombre']}")

def ejercicio_8():
    """Ejercicio 8: Buscar en una lista de diccionarios"""
    print("\n--- EJERCICIO 8: BUSCAR EN LISTA DE DICCIONARIOS ---")
    
    estudiantes = [
        {"nombre": "Juan", "edad": 20, "curso": "Python"},
        {"nombre": "María", "edad": 19, "curso": "JavaScript"},
        {"nombre": "Carlos", "edad": 21, "curso": "Python"},
        {"nombre": "Ana", "edad": 22, "curso": "JavaScript"}
    ]
    
    curso_buscar = input("¿Qué curso buscas? ")
    
    print(f"\nEstudiantes en el curso '{curso_buscar}':")
    encontrados = False
    for estudiante in estudiantes:
        if estudiante['curso'].lower() == curso_buscar.lower():
            print(f"  {estudiante['nombre']}")
            encontrados = True
    
    if not encontrados:
        print("  No se encontraron estudiantes en ese curso.")

def ejercicio_9():
    """Ejercicio 9: Diccionario con listas como valores"""
    print("\n--- EJERCICIO 9: DICCIONARIO CON LISTAS COMO VALORES ---")
    
    materias = {
        "Matemáticas": ["Juan", "María", "Carlos"],
        "Física": ["Ana", "Luis", "Pedro"],
        "Química": ["María", "Carlos", "Ana", "Juan"]
    }
    
    print("Materias y estudiantes:")
    for materia, estudiantes in materias.items():
        print(f"{materia}: {', '.join(estudiantes)}")
    
    # Mostrar estudiantes de una materia específica
    materia_buscar = "Química"
    print(f"\nEstudiantes en {materia_buscar}:")
    for estudiante in materias[materia_buscar]:
        print(f"  {estudiante}")

def ejercicio_10():
    """Ejercicio 10: Diccionario anidado"""
    print("\n--- EJERCICIO 10: DICCIONARIO ANIDADO ---")
    
    estudiantes = {
        1: {"nombre": "Juan", "edad": 20, "notas": [8.5, 9.0, 7.5]},
        2: {"nombre": "María", "edad": 19, "notas": [9.2, 8.8, 9.5]},
        3: {"nombre": "Carlos", "edad": 21, "notas": [7.0, 8.0, 8.5]},
        4: {"nombre": "Ana", "edad": 20, "notas": [9.0, 9.3, 8.7]},
        5: {"nombre": "Luis", "edad": 22, "notas": [8.2, 7.8, 8.9]}
    }
    
    print("Promedio de notas de cada estudiante:")
    for id_estudiante, info in estudiantes.items():
        promedio = sum(info["notas"]) / len(info["notas"])
        print(f"  {info['nombre']}: {promedio:.2f}")

def main():
    """Función principal del programa"""
    opciones = {
        "1": ejemplo_listas,
        "2": ejemplo_tuplas,
        "3": ejemplo_arreglos,
        "4": ejemplo_diccionarios,
        "5": ejemplo_lista_diccionarios,
        "6": ejemplo_diccionarios_anidados,
        "7": ejercicio_1,
        "8": ejercicio_2,
        "9": ejercicio_3,
        "10": ejercicio_4,
        "11": ejercicio_5,
        "12": ejercicio_6,
        "13": ejercicio_7,
        "14": ejercicio_8,
        "15": ejercicio_9,
        "16": ejercicio_10
    }
    
    while True:
        limpiar_pantalla()
        mostrar_menu()
        
        opcion = input("\nSelecciona una opción (0-16): ").strip()
        
        if opcion == "0":
            print("\n¡Gracias por usar el programa! ¡Hasta pronto!")
            break
        elif opcion in opciones:
            limpiar_pantalla()
            opciones[opcion]()
            pausa()
        else:
            print("\nOpción no válida. Por favor, selecciona una opción del 0 al 16.")
            pausa()

if __name__ == "__main__":

    main()
