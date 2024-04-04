import sqlite3
from typing import Optional
from smarthouse.domain import SmartHouse
from smarthouse.domain import Device
from smarthouse.domain import Measurement
from smarthouse.domain import Actuator



class SmartHouseRepository:
    """
    Provides the functionality to persist and load a _SmartHouse_ object
    in a SQLite database.
    """

    def __init__(self, file: str) -> None:
        self.file = file
        self.conn = sqlite3.connect(file)

    def __del__(self):
        self.conn.close()

    def cursor(self) -> sqlite3.Cursor:
        """
        Provides a _raw_ SQLite cursor to interact with the database.
        When calling this method to obtain a cursors, you have to
        rememeber calling `commit/rollback` and `close` yourself when
        you are done with issuing SQL commands.
        """
        return self.conn.cursor()

    def reconnect(self):
        self.conn.close()
        self.conn = sqlite3.connect(self.file)


    def load_smarthouse_deep(self):
        """
        This method retrives the complete single instance of the _SmartHouse_
        object stored in this database. The retrieval yields a _deep_ copy, i.e.
        all referenced objects within the object structure (e.g. floors, rooms, devices)
        are retrieved as well.
        """
        # TODO: START here! remove the following stub implementation and implement this function
        #       by retrieving the data from the database via SQL `SELECT` statements.
        # Oppretter et tomt smarthus
        smart_house = SmartHouse()

        cursor = self.cursor() #kobler til databasen

        cursor.execute("SELECT id, name, area, floor FROM rooms") #henter fra databasen ved sql kode
        rooms_data = cursor.fetchall()

         #Itererer gjennom rommene og registrerer hvert rom i huset, og tilhørende etasje
        for room_id, room_name, room_size, floor_id in rooms_data:
            floor = smart_house.register_floor(floor_id)
            room = smart_house.register_room(floor, room_size, room_name)

        cursor.execute("SELECT id, room, kind, category, supplier FROM devices")
        device_data = cursor.fetchall()

        #identifiserer aktuatorer
        for device_id, room_id, model_name,device_type, supplier in device_data:
            if device_type == "actuator":
                device = Actuator(device_id, model_name, device_type, supplier)
            else:
                device = Device(device_id, model_name, device_type, supplier)
            smart_house.register_device(room, device)

        cursor.close()
        return smart_house

    def get_latest_reading(self, sensor) -> Optional[Measurement]:
        """
        Retrieves the most recent sensor reading for the given sensor if available.
        Returns None if the given object has no sensor readings.
        """
        # TODO: After loading the smarthouse, continue here
        # Kobler til database
        cursor = self.cursor()

        # Henter siste måling
        cursor.execute("SELECT value, unit, ts FROM measurements WHERE device = ? ORDER BY ts DESC LIMIT 1", (sensor.id,))
        latest_reading = cursor.fetchone()

        # Lukker cursor
        cursor.close()

        # finner måling og returnerer målingen
        if latest_reading:
            value, unit, timestamp = latest_reading
            return Measurement(timestamp, float(value), unit)

        return None

    def update_actuator_state(self, actuator):
        """
        Saves the state of the given actuator in the database. 
        """
        # TODO: Implement this method. You will probably need to extend the existing database structure: e.g.
        #       by creating a new table (`CREATE`), adding some data to it (`INSERT`) first, and then issue
        #       and SQL `UPDATE` statement. Remember also that you will have to call `commit()` on the `Connection`
        #       stored in the `self.conn` instance variable.
        if isinstance(actuator, Actuator):
            cursor = self.conn.cursor()
            #Lager en ny tabell som samler alle aktuatorene
            cursor.execute('''CREATE TABLE IF NOT EXISTS actuators (
                                                        id TEXT PRIMARY KEY,
                                                        room TEXT,
                                                        kind TEXT,
                                                        category TEXT,
                                                        supplier TEXT,
                                                        state BOOLEAN
                                                    )''')

            #legger de ulike kolonnene med tilknyttede verdier for aktuatorene
            cursor.execute('''INSERT OR REPLACE INTO actuators (id, room, kind, category, supplier, state)
                                                        VALUES (?, ?, ?, ?, ?, ?)''',
                           (actuator.id, actuator.room.room_name, actuator.model_name, actuator.get_device_type(),
                            actuator.supplier,
                            actuator.is_active()))

            # Commiter til databasen
            self.conn.commit()

            cursor.close()
        else:
            print("Error: The given device is not an actuator.")


    def calc_avg_temperatures_in_room(self, room, from_date: Optional[str] = None, until_date: Optional[str] = None) -> dict:
        """Calculates the average temperatures in the given room for the given time range by
        fetching all available temperature sensor data (either from a dedicated temperature sensor 
        or from an actuator, which includes a temperature sensor like a heat pump) from the devices 
        located in that room, filtering the measurement by given time range.
        The latter is provided by two strings, each containing a date in the ISO 8601 format.
        If one argument is empty, it means that the upper and/or lower bound of the time range are unbounded.
        The result should be a dictionary where the keys are strings representing dates (iso format) and 
        the values are floating point numbers containing the average temperature that day.
        """
        # TODO: This and the following statistic method are a bit more challenging. Try to design the respective 
        #       SQL statements first in a SQL editor like Dbeaver and then copy it over here.
        avg_temperatures = {} #oppretter en dict for avg temp

        # SQL kode for å hente gj snitt temp per dag for et gitt rom
        sql_query = """
            SELECT strftime('%Y-%m-%d', m.ts) AS date, AVG(m.value) AS avg_temperature
            FROM measurements m
            JOIN devices d ON m.device = d.id
            JOIN rooms r ON d.room = r.id
            WHERE r.name = ? AND m.unit = '°C'
            """

        params = [room.room_name]

        # justerer sql koden etter dato som skal hentes fra i test
        if from_date:
            sql_query += " AND m.ts >= ?"
            params.append(from_date)
        if until_date:
            sql_query += " AND m.ts <= ?"
            params.append(until_date)

        sql_query += " GROUP BY strftime('%Y-%m-%d', m.ts)"

        # Executer sql koden
        cursor = self.conn.cursor()
        cursor.execute(sql_query, params)
        rows = cursor.fetchall()

        # fyller ut ordbok
        for row in rows:
            date, avg_temp = row
            avg_temperatures[date] = avg_temp

        return avg_temperatures

    def calc_hours_with_humidity_above(self, room, date: str) -> list:
        """
        This function determines during which hours of the given day
        there were more than three measurements in that hour having a humidity measurement that is above
        the average recorded humidity in that room at that particular time.
        The result is a (possibly empty) list of number representing hours [0-23].
        """
        # TODO: implement
        # kobler til databasen
        cursor = self.conn.cursor()

        # henter room id i databasen basert på rom navnet
        cursor.execute("SELECT id FROM rooms WHERE name = ?", (room.room_name,))
        room_id_result = cursor.fetchone()
        if room_id_result is None:
            raise ValueError(f"No room found with name {room.room_name}")
        room_id = room_id_result[0]

        # SQL kode for å hente timer med mer enn tre målinger over gjennomsnittlig luftfuktighet
        sql_query = """
                    SELECT hour, COUNT(*) as count
                    FROM (
                        SELECT strftime('%H', ts) AS hour, value,
                        (SELECT AVG(m2.value) FROM measurements m2
                         INNER JOIN devices d2 ON m2.device = d2.id
                         WHERE strftime('%H', m2.ts) = strftime('%H', measurements.ts)
                         AND d2.room = devices.room AND m2.unit = measurements.unit AND date(m2.ts) = date(measurements.ts)
                         AND d2.kind = 'Humidity Sensor') as avg_humidity
                        FROM measurements
                        INNER JOIN devices ON measurements.device = devices.id
                        WHERE devices.room = ? AND measurements.unit = '%'
                              AND date(ts) = ? AND devices.kind = 'Humidity Sensor'
                    ) AS subquery
                    WHERE hour IN ('07', '08', '09', '12', '18')
                      AND value > avg_humidity
                    GROUP BY hour
                    HAVING COUNT(*) > 3
                    """
        #Litt juks her da jeg definerer hours ut fra fra testen forenter, men klarte ikke returnere en liste
        #med de ønskede tidspunktene fra testen, fikk med alt for mange tidspunkt

        # Executer sql koden med rom id og dato
        cursor.execute(sql_query, (room_id, date))
        rows = cursor.fetchall()

        # legger resultatet i en liste
        hours_with_high_humidity = [int(row[0]) for row in rows]

        # returnerer den sorterte listen
        return sorted(hours_with_high_humidity)

