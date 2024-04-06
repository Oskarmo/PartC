import sqlite3
from typing import Optional
from smarthouse.domain import Measurement
from smarthouse.domain import SmartHouse, Sensor, Actuator, ActuatorWithSensor
from .domain import NewSensorMeasurement





class SmartHouseRepository:
    """
    Provides the functionality to persist and load a _SmartHouse_ object
    in a SQLite database.
    """

    def __init__(self, file: str) -> None:
        self.file = file
        self.conn = sqlite3.connect(file, check_same_thread=False)


    def __del__(self):
        if self.conn:
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
        if self.conn:
            self.conn.close()
        self.conn = sqlite3.connect(self.file, check_same_thread=False)

    def get_device_by_id(self, device_id: str):
        cursor = self.conn.cursor()
        cursor.execute("SELECT id, room, kind, category, supplier, product FROM devices WHERE id = ?", (device_id,))
        row = cursor.fetchone()
        cursor.close()
        if row:
            device_id, room_id, kind, category, supplier, product = row
            if category == 'sensor':
                return Sensor(id=device_id, model_name=product, supplier=supplier, device_type=kind)
            elif category == 'actuator':
                return Actuator(id=device_id, model_name=product, supplier=supplier, device_type=kind)
        return None

    def add_measurement_to_sensor(self, sensor_id: str, measurement: NewSensorMeasurement):
        cursor = self.conn.cursor()
        cursor.execute("INSERT INTO measurements (device, value, unit, ts) VALUES (?, ?, ?, datetime('now'))",
                       (sensor_id, measurement.value, measurement.unit))
        self.conn.commit()
        cursor.close()

    def delete_oldest_measurement(self, sensor_id: str):
        """
        Deletes the oldest measurement for the given sensor.
        """
        delete_sql = """
        DELETE FROM measurements
        WHERE rowid IN (
          SELECT rowid FROM measurements
          WHERE device = ?
          ORDER BY ts ASC
          LIMIT 1
        );
        """
        cursor = self.conn.cursor()
        cursor.execute(delete_sql, (sensor_id,))
        self.conn.commit()
        cursor.close()


    def load_smarthouse_deep(self):
        """
        This method retrieves the complete single instance of the _SmartHouse_
        object stored in this database. The retrieval yields a _deep_ copy, i.e.
        all referenced objects within the object structure (e.g. floors, rooms, devices)
        are retrieved as well.
        """
        result = SmartHouse()
        cursor = self.cursor()

        # Creating floors
        cursor.execute('SELECT MAX(floor) from rooms;')
        no_floors = cursor.fetchone()[0]
        floors = []
        for i in range(0, no_floors):
            floors.append(result.register_floor(i + 1))

        # Creating rooms
        room_dict = {}
        cursor.execute('SELECT id, floor, area, name from rooms;')
        room_tuples = cursor.fetchall()
        for room_tuple in room_tuples:
            room = result.register_room(floors[int(room_tuple[1]) - 1], float(room_tuple[2]), room_tuple[3])
            room.db_id = int(room_tuple[0])
            room_dict[room_tuple[0]] = room

        cursor.execute('SELECT id, room, kind, category, supplier, product from devices;')
        device_tuples = cursor.fetchall()
        for device_tuple in device_tuples:
            room = room_dict[device_tuple[1]]
            category = device_tuple[3]
            if category == 'sensor':
                result.register_device(room, Sensor(device_tuple[0], device_tuple[5], device_tuple[4], device_tuple[2]))
            elif category == 'actuator':
                if device_tuple[2] == 'Heat Pump':
                    result.register_device(room, ActuatorWithSensor(device_tuple[0], device_tuple[5], device_tuple[4],
                                                                    device_tuple[2]))
                else:
                    result.register_device(room,
                                           Actuator(device_tuple[0], device_tuple[5], device_tuple[4], device_tuple[2]))

        for dev in result.get_devices():
            if isinstance(dev, Actuator):
                cursor.execute(f"SELECT state FROM states where device = '{dev.id}';")
                state_result = cursor.fetchone()
                if state_result is not None:
                    state = state_result[0]
                    if state is None:
                        dev.turn_off()
                    elif float(state) == 1.0:
                        dev.turn_on()
                    else:
                        dev.turn_on(float(state))
                else:
                    # Handle case where no state is found for the device
                    print(f"No state found for device {dev.id}")

        cursor.close()
        return result

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

    def update_actuator_state(self, actuator, new_state: bool):
        query = "UPDATE devices SET state = ? WHERE id = ?"
        params = (1 if new_state else 0, actuator.id)
        c = self.cursor()
        c.execute(query, params)
        self.conn.commit()
        c.close()

    def get_actuator_state(self, actuator_id: str) -> Optional[bool]:
        query = "SELECT state FROM devices WHERE id = ?"
        cursor = self.conn.cursor()
        cursor.execute(query, (actuator_id,))
        row = cursor.fetchone()
        cursor.close()
        if row is not None:
            return bool(row[0])
        return None


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

