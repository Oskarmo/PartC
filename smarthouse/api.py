import uvicorn
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from smarthouse.persistence import SmartHouseRepository
from pathlib import Path
from pydantic import BaseModel
from typing import List, Optional
from typing import Union
from fastapi import HTTPException, Body, Query
from smarthouse.domain import Sensor


import os
def setup_database():
    project_dir = Path(__file__).parent.parent
    db_file = project_dir / "data" / "db.sql" # you have to adjust this if you have changed the file name of the database
    return SmartHouseRepository(str(db_file.absolute()))

app = FastAPI()

repo = setup_database()

smarthouse = repo.load_smarthouse_deep()

if not (Path.cwd() / "www").exists():
    os.chdir(Path.cwd().parent)
if (Path.cwd() / "www").exists():
    # http://localhost:8000/welcome/index.html
    app.mount("/static", StaticFiles(directory="www"), name="static")


# http://localhost:8000/ -> welcome page
@app.get("/")
def root():
    return RedirectResponse("/static/index.html")


# Health Check / Hello World
@app.get("/hello")
def hello(name: str = "world"):
    return {"hello": name}


# Starting point ...

@app.get("/smarthouse")
def get_smarthouse_info() -> dict[str, int | float]:
    """
    This endpoint returns an object that provides information
    about the general structure of the smarthouse.
    """
    return {
        "no_rooms": len(smarthouse.get_rooms()),
        "no_floors": len(smarthouse.get_floors()),
        "registered_devices": len(smarthouse.get_devices()),
        "area": smarthouse.get_area()
    }

# TODO: implement the remaining HTTP endpoints as requested in
# https://github.com/selabhvl/ing301-projectpartC-startcode?tab=readme-ov-file#oppgavebeskrivelse
# here ...

class FloorModel(BaseModel):
    level: int
    rooms: Optional[List[str]] = []  # returnerer kun liste med floors og romnavn tilhørende floors


class RoomModel(BaseModel):
    room_name: str
    room_size: float
    devices: Optional[List[str]] = []

class DeviceModel(BaseModel):
    id: str
    model_name: str
    device_type: str
    supplier: str

class SensorModel(DeviceModel):
    unit: str
    last_measurement: Optional[float] = None

class ActuatorModel(DeviceModel):
    state: Union[bool, float, None]

class MeasurementModel(BaseModel):
    timestamp: str
    value: float
    unit: str

class CurrentSensorMeasurement(BaseModel):
    timestamp: str
    value: float
    unit: str

class NewSensorMeasurement(BaseModel):
    value: float
    unit: str


@app.get("/smarthouse/floor", response_model=List[FloorModel])
def get_floors():
    floors = smarthouse.get_floors()
    print(f"Floors in the smarthouse: {floors}")
    return [FloorModel(level=floor.level, rooms=[room.room_name for room in floor.rooms]) for floor in floors]

@app.get("/smarthouse/floor/{fid}", response_model=FloorModel)
def get_floor(fid: int):
    print(f"Requested floor ID: {fid}")
    floors = smarthouse.get_floors()
    print(f"Available floors: {[floor.level for floor in floors]}")
    floor = next((f for f in floors if f.level == fid), None)
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    print(f"Returning floor: {floor.level} with rooms: {[room.room_name for room in floor.rooms]}")
    return FloorModel(level=floor.level, rooms=[room.room_name for room in floor.rooms])

@app.get("/smarthouse/floor/{fid}/room", response_model=List[RoomModel])
def get_rooms(fid: int):
    floor = next((f for f in smarthouse.get_floors() if f.level == fid), None)
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    return [RoomModel(room_name=room.room_name, room_size=room.room_size, devices=[device.id for device in room.devices]) for room in floor.rooms]

# Information about a specific room rid on a given floor fid
@app.get("/smarthouse/floor/{fid}/room/{rid}", response_model=RoomModel)
def get_room(fid: int, rid: str):
    floor = next((f for f in smarthouse.get_floors() if f.level == fid), None)
    if not floor:
        raise HTTPException(status_code=404, detail="Floor not found")
    room = next((r for r in floor.rooms if r.room_name == rid), None)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return RoomModel(room_name=room.room_name, room_size=room.room_size, devices=[device.id for device in room.devices])

# Information on all devices
@app.get("/smarthouse/device", response_model=List[DeviceModel])
def get_devices():
    return [DeviceModel(id=device.id, model_name=device.model_name, device_type=device.get_device_type(), supplier=device.supplier) for device in smarthouse.get_devices()]

# Information for a given device identified by uuid
@app.get("/smarthouse/device/{uuid}", response_model=Union[SensorModel, ActuatorModel])
def get_device(uuid: str):
    device = smarthouse.get_device_by_id(uuid)
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    if device.is_sensor():
        return SensorModel(id=device.id, model_name=device.model_name, device_type=device.get_device_type(), supplier=device.supplier, unit=device.unit)
    elif device.is_actuator():
        return ActuatorModel(id=device.id, model_name=device.model_name, device_type=device.get_device_type(), supplier=device.supplier, state=device.state)
    else:
        raise HTTPException(status_code=400, detail="Device type unknown")

# Get current sensor measurement
@app.get("/smarthouse/sensor/{uuid}/current")
def get_current_sensor_measurement(uuid: str):
    sensor = repo.get_device_by_id(uuid)
    if not isinstance(sensor, Sensor):
        raise HTTPException(status_code=404, detail="Sensor not found")
    latest_reading = repo.get_latest_reading(sensor)
    if not latest_reading:
        raise HTTPException(status_code=404, detail="Latest reading not found")
    return latest_reading

# Add a new measurement for sensor
@app.post("/smarthouse/sensor/{uuid}/current")
def add_measurement_for_sensor(uuid: str, measurement: NewSensorMeasurement):
    sensor = repo.get_device_by_id(uuid)
    if sensor is None or not isinstance(sensor, Sensor):
        raise HTTPException(status_code=404, detail="Sensor not found")

    repo.add_measurement_to_sensor(sensor.id, measurement)
    return {"message": "Measurement added successfully"}

# Get n latest available measurements for sensor
@app.get("/smarthouse/sensor/{uuid}/values", response_model=List[CurrentSensorMeasurement])
def get_latest_sensor_measurements(uuid: str):
    sensor = repo.get_device_by_id(uuid)
    if not sensor or not isinstance(sensor, Sensor):
        raise HTTPException(status_code=404, detail="Sensor not found")

    latest_measurement = repo.get_latest_reading(sensor)
    if not latest_measurement:
        return []

    return [CurrentSensorMeasurement(timestamp=latest_measurement.timestamp, value=latest_measurement.value,
                                     unit=latest_measurement.unit)]

# Delete the oldest measurement for sensor

@app.delete("/smarthouse/sensor/{uuid}/oldest")
def delete_oldest_measurement_for_sensor(uuid: str):
    sensor = repo.get_device_by_id(uuid)
    if not sensor or not isinstance(sensor, Sensor):
        raise HTTPException(status_code=404, detail="Sensor not found")

    repo.delete_oldest_measurement(sensor.id)
    return {"message": "Oldest measurement deleted successfully"}


if __name__ == '__main__':
    uvicorn.run(app, host="127.0.0.1", port=8000)


