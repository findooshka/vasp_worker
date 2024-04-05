import requests
import time
from flask import Flask, request, jsonify
from threading import Thread, Lock
from datetime import datetime, timedelta
from werkzeug.utils import secure_filename
import os
import vasp
from transitions import Machine
import subprocess
import json

SERVER_URL = "http://10.40.2.38:8125"

class TaskState:
    states = ['pending', 'processing', 'completed', 'error']

    def __init__(self):
        self.machine = Machine(model=self, states=TaskState.states, initial='pending')
        self.id = None
        self.file_name = None

        # Adding transitions
        self.machine.add_transition(trigger='process', source='pending', dest='processing')
        self.machine.add_transition(trigger='complete', source='processing', dest='completed')
        self.machine.add_transition(trigger='send', source=['completed', 'error'], dest='pending')
        self.machine.add_transition(trigger='fail', source=['pending', 'processing', 'completed'], dest='error')

    def accept(self, task_id):
        self.id = task_id
        self.process()

task = TaskState()
pause_time = None
worker_id = None
lock = Lock()  # protects the task (state), worker_id and the pause_time

class TaskHandler:
    def __init__(self):
        self.result = {}
        self.task_id = None
        self.file_name = None

    def report_task_completion(self):
        with lock:
            global worker_id
            data = {
                "worker_id": worker_id,
                "result": json.dumps(self.result),
                "state": task.state
            }
            if self.result is not None:
                files = {
                    'OSZICAR': open(os.path.join("vasp", "OSZICAR"), 'rb'),
                    'vasp_output.txt': open(os.path.join("vasp", "vasp_output.txt"), 'rb'),
                    'CONTCAR': open(os.path.join("vasp", "CONTCAR"), 'rb'),
                }
                if os.path.exists(os.path.join("vasp", "ELFCAR")):
                    files['ELFCAR'] = open(os.path.join("vasp", "ELFCAR"), 'rb')
            else:
                files = {}
                if os.path.exists(os.path.join("vasp", "vasp_output.txt")):
                    files['vasp_output.txt'] = open(os.path.join("vasp", "vasp_output.txt"), 'rb')
        try:
            response = requests.post(f"{SERVER_URL}/vasp/report_task/{task.id}", data=data, files=files)
        except Exception as e:
            print(f"Error reporting task completion: {e}")
            return False
        if response.status_code == 200:
            with lock:
                print(f"Successfully reported completion of task {task.id}")
                task.send()
            self.result = None
            return True
        try:
            print(f"Error reporting task completion {response.json()['message']}")
        except:
            print(f"Error reporting task completion")
        return False
        

    def _execute(self):
        # actually do the task
        try:
            energy = vasp.vasp_energy()  # doing the task
        except Exception as e:
            print(f"Error while calculating: {e}")
            self.result = str(e)
            with lock:
                task.fail()
        else:
            self.result = energy
            with lock:
                task.complete()

    def execute(self):
        # default program loop
        with lock:
            task_state = task.state
            task_id = task.id
        if task_state == "completed" or task_state == "error":
            self.report_task_completion()
            time.sleep(1)
        elif task_state == 'processing':
            self._execute()
        else:
            time.sleep(1)
            return

worker_app = Flask(__name__)

task_handler = TaskHandler()  # should not be accessed by the server thread

def count_processes(names):
    pattern = '|'.join(names)
    
    ps = subprocess.Popen(["ps", "-efww"], stdout=subprocess.PIPE)
    grep = subprocess.Popen(["grep", "-E", pattern], stdin=ps.stdout, stdout=subprocess.PIPE)
    ps.stdout.close()
    
    output = subprocess.check_output(["wc", "-l"], stdin=grep.stdout)
    grep.stdout.close()
    
    result = output.decode('utf-8').strip()
    
    return int(result) - 1

def pause():
    with lock:
        global pause_time
        pause_time = datetime.now()
        print('Pausing')

def is_paused():
    with lock:
        global pause_time
        if pause_time is None:
            return False
        if datetime.now() - pause_time > timedelta(hours=1):
            pause_time = None
            return False
        return True

@worker_app.route('/task', methods=['POST'])
def receive_task():
    if count_processes(['vasp', 'mcsqs']) != 0:
        pause()
    if is_paused():
        return jsonify({"status": "error", "message": "The worker is paused"}), 400
    
    with lock:
        if task.state != "pending":
            return jsonify({"status": "error", "message": "The worker is busy"}), 400

        if 'file' not in request.files:
            return jsonify({"status": "error", "message": "No file part in the request"}), 400

        file = request.files['file']

        if file.filename == '':
            return jsonify({"status": "error", "message": "No selected file"}), 400

        if file:
            filename = secure_filename(file.filename)
            os.makedirs('vasp', exist_ok=True)
            file_path = os.path.join('vasp', filename)
            incar_path = os.path.join('vasp', 'INCAR')
            kpoints_path = os.path.join('vasp', 'KPOINTS')
            reference_energies_path = os.path.join('vasp', 'reference_energies.yml')
            file.save(file_path)

            task.accept(request.form.get('task_id'))
            with open(incar_path, 'w') as f:
                f.write(request.form.get('incar'))
            with open(kpoints_path, 'w') as f:
                f.write(request.form.get('kpoints'))
            with open(reference_energies_path, 'w') as f:
                f.write(request.form.get('reference_energies'))

            return jsonify({"status": "success", "message": "File received and saved"}), 200
    
    return jsonify({"status": "error", "message": "No file"}), 400

@worker_app.route('/control', methods=['POST'])
def control_worker():
    command = request.json.get('command')
    if command == 'pause':
        pause()
        return jsonify({"status": "success", "message": "Worker paused."})
    # Add other commands as needed
    return jsonify({"status": "error", "message": "Unknown command."})

@worker_app.route('/check', methods=['POST'])
def check_worker():
    # the server is a worrywart, they check on their workers from time to time
    # hafta tell them we're alright :)
    pause = "paused" if is_paused() else "running"
    with lock:
        idle = "idle" if task.state == "pending" else "busy"
    return jsonify({"status": "I am okay ^^", "paused": pause, "idle": idle})

def run_worker_server():
    worker_app.run(host='0.0.0.0', port=8130)

def register_worker():
    while True:
        try:
            response = requests.post(f"{SERVER_URL}/vasp/volunteer", json=[])
        except:
            print("Could not register the worker, going to sleep")
        else:
            if response.status_code == 200:
                with lock:
                    global worker_id
                    worker_id = response.json()['assigned_id']
                #return True
            else:
                print("Could not register the worker, going to sleep")
        time.sleep(30)
        
if __name__ == "__main__":
    #while not register_worker():
    #    pass
    #print(f"Registered with worker ID: {worker_id}")
    Thread(target=register_worker, daemon=True).start()
    Thread(target=run_worker_server, daemon=True).start()
    while True:
        task_handler.execute()
