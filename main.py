from flask import Flask, render_template, request, redirect, url_for, json, jsonify, flash
#from src.job import Job
import src.queue as q
import os
import signal
import base64
from celery import Celery
import subprocess
from src.system import System
from celery.utils.log import get_task_logger
import random
import time
import fnmatch
import subprocess
from subprocess import Popen, PIPE, CalledProcessError
import sys
import re
import threading
import gzip
import glob
import argparse
import redis
import traceback
import functools
import inspect
import pandas as pd


VERSION = "0.3"
ARTIC_VERSION = "1.2.1"

pd.set_option('display.width', 1000)
pd.set_option('colheader_justify', 'center')


class MyParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('error: %s\n' % message)
        self.print_help()
        sys.exit(2)

redis_port = sys.argv[1]

app = Flask(__name__)
app.config['SECRET_KEY'] = 'top-secret!'

# Celery configuration
# app.config['CELERY_BROKER_URL'] = 'redis://localhost:7777/0'
# app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:7777/0'
app.config['CELERY_BROKER_URL'] = 'redis://localhost:{}/0'.format(redis_port)
app.config['CELERY_RESULT_BACKEND'] = 'redis://localhost:{}/0'.format(redis_port)
app.secret_key = "shhhh"

# Initialize Celery
# celery = Celery(app.name)
celery = Celery(app.name, broker=app.config['CELERY_BROKER_URL'])
celery.conf.update(app.config)

logger = get_task_logger(__name__)

#Define maximum queue size
max_queue_size = 10

#Create a System object with a queue of length maximum_queue_size
qSys = System(max_queue_size)

if fnmatch.fnmatch(sys.argv[0], "*celery"):
    test_arg = False
    for a in sys.argv:
        if a == "-b":
            test_arg = True
            continue
        if test_arg:
            redis_port_arg = a
            break
    # worker_port = int(sys.argv[5].split(":")[2].split("/")[0])
    worker_port = int(redis_port_arg.split(":")[2].split("/")[0])
    red = redis.StrictRedis(host='localhost', port=worker_port, db=0)

#Global variable for base filepath
#initialised as /user/data
# plot_file = os.path.dirname(os.path.realpath(__file__))+'/plots.py'
config_file = os.path.dirname(os.path.realpath(__file__))+'/config.init'
primer_folder = os.path.dirname(os.path.realpath(__file__))+'/primer-schemes'
with open(config_file) as f:
        data = json.load(f)
input_filepath = data['data-folder']
sample_csv = data['sample-barcode-csvs']
schemes = {}
# nCoV-2019 schemes
schemes['nCoV_2019_eden_V1_scheme'] = os.path.join(primer_folder, "eden")
schemes['nCoV_2019_eden_V1_scheme_name'] = "nCoV-2019/V1"
schemes['nCoV_2019_midnight_V1_scheme'] = os.path.join(primer_folder, "midnight")
schemes['nCoV_2019_midnight_V1_scheme_name'] = "nCoV-2019/V1"
schemes['nCoV_2019_artic_V1_scheme'] = os.path.join(primer_folder, "artic")
schemes['nCoV_2019_artic_V1_scheme_name'] = "nCoV-2019/V1"
schemes['nCoV_2019_artic_V2_scheme'] = os.path.join(primer_folder, "artic")
schemes['nCoV_2019_artic_V2_scheme_name'] = "nCoV-2019/V2"
schemes['nCoV_2019_artic_V3_scheme'] = os.path.join(primer_folder, "artic")
schemes['nCoV_2019_artic_V3_scheme_name'] = "nCoV-2019/V3"

# ZaireEbola shemes
schemes['IturiEBOV_artic_V1_scheme'] = os.path.join(primer_folder, "artic")
schemes['IturiEBOV_artic_V1_scheme_name'] = "IturiEBOV/V1"

@app.route('/getCheckTasksUrl', methods = ['POST'])
def getCheckTasksUrl():
    return jsonify({}), 202, {'Location': url_for('checkTasks')}

@app.route('/checkTasks')
def checkTasks():
    queueList = []
    completedList = []
    changed = False

    for job in qSys.queue.getItems():
        if job.task_id:
            task = executeJob.AsyncResult(job.task_id)
            if task.ready():
                qSys.moveJobToComplete(job.job_name)
                changed = True
                #Don't add this job to queueList (we don't want it to display in the queue)
                continue
        queueList.append({job.job_name : url_for('progress', job_name=job.job_name, task_id = job.task_id)})

    for job in qSys.completed:
        completedList.append({job.job_name : url_for('delete', job_name=job.job_name)})

    queueDict = {'jobs': queueList}
    for key, value in queueDict.items():
        print(key, value)

    completedDict = {'jobs': completedList}
    for key, value in completedDict.items():
        print(key, value)

    return json.htmlsafe_dumps({'changed': changed, 'queue': queueDict, 'completed': completedDict})


def check_override(output_folder, override_data, skip):
    print("Checking output folder:::", output_folder)
    if(not os.path.exists(output_folder)):
        if skip > 0:
            return False
        return True
    dir_files = os.listdir(output_folder)
    if len(dir_files) > 1 and override_data is False:
        if skip > 0:
            return False
        return True
    elif len(dir_files) == 1 and dir_files[0] == "all_cmds_log.txt":
        print("checking files:::",dir_files)
        if os.path.getsize(output_folder+"/all_cmds_log.txt") > 0:
            if skip > 0:
                return False
            return True
    return False


@celery.task(bind=True)
def executeJob(self, job_name, gather_cmd, demult_cmd, min_cmd, plot_cmd, step):
    logger.info("In celery task, executing job...")
    logger.info("executing job_name: {}".format(job_name))
    logger.info("Starting from step: {}".format(step))
    # Step is a debug command to start at 0, 1, 2, 3 in the commands list with
    # an existing job_name, as it should build all the commands as usual
    # but not execute them, so if I just want to do plots, I can use skip=3

    # group ID to kill children
    # {"job_name": #####}
    Anakin = {}

    self.update_state(state='PROGRESS', meta={'current':10, 'status':'Beginning execution'})

    commands = [gather_cmd, demult_cmd, min_cmd, plot_cmd]
    for i, cmd in enumerate(commands[step:]):
        po = subprocess.Popen(cmd, shell=True, preexec_fn=os.setsid,
                            stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)

        Anakin[job_name] = po.pid
        rval = json.dumps(Anakin)
        red.set(str(job_name), rval)
        # k = ["{}: {}".format(key, Anakin[key]) for key in Anakin.keys()]
        # sys.stderr.write(",".join(k))
        # sys.stderr.write("\n")

        stdout, stderr = po.communicate()
        #self.update_state(state='PROGRESS')
        po.wait()

        if i == 0:
            status = "Successfully ran gather"
            n = 50
        else:
            status = "Successfully ran minion"
            n = 90

        self.update_state(state='PROGRESS', meta={'current': n, 'status': status, 'command': cmd})
        returnCode = po.returncode
        if returnCode != 0:
            self.update_state(state='FAILURE', meta={'exc_type': type(ex).__name__, 'exc_message': traceback.format_exc().split('\n'), 'current': n, 'status': 'Command failed', 'command': cmd})
            raise Exception("Command {} got return code {}.\nSTDOUT: {}\nSTDERR: {}".format(cmd, returnCode, stdout, stderr))
            break

        print("JOB CMD {} RETURNED: {}".format(cmd, returnCode))

    self.update_state(state='FINISHED', meta={'current': 100, 'status': 'Finishing', 'result': returnCode}) #Don't know if this is actually used
    return {'current': 100, 'total': 100, 'status': 'Task completed!', 'result': returnCode}

@celery.task(bind=True)
def killJob(self, job_name):
    logger.info("In celery task, executing job...")
    logger.info("killing job_name: {}".format(job_name))
    pidss = red.get(str(job_name))
    Anakin = json.loads(pidss)
    if not Anakin:
        sys.stderr.write("ANAKIN EMPTY!!!\n")
    else:
        # k = ["{}: {}".format(key, Anakin[key]) for key in Anakin.keys()]
        # sys.stderr.write(",".join(k))
        try:
            # k = ["{}: {}".format(key, Anakin[key]) for key in Anakin.keys()]
            # sys.stderr.write(",".join(k))
            group_pid = Anakin[job_name]
            sys.stderr.write("killing PID: {}\n".format(group_pid))
            os.killpg(group_pid, signal.SIGTERM)
        except:
            traceback.print_exc()
            sys.stderr.write("killJob FAILED - 1")
            sys.stderr.write("\n")
            return 1
        sys.stderr.write("killJob SUCCESS - 0")
        sys.stderr.write("\n")
        return 0
    sys.stderr.write("killJob FAILED (ANAKIN EMPTY) - 1")
    sys.stderr.write("\n")
    return 1

@app.route('/task/<job_name>', methods = ['POST'])
def task(job_name):
    job = qSys.getJobByName(job_name)
    return jsonify({}), 202, {'Location': url_for('task_status', task_id = job.task_id, job_name = job.job_name)}

@app.route('/status/<task_id>')
def task_status(task_id):
    task = executeJob.AsyncResult(task_id)
    print("TASK.READY: ", task.ready())
    if task.state == 'PENDING':
        response = {
            'state': task.state,
            'current': 0,
            'total': 100,
            'status': 'Pending...'
        }
    elif task.state != 'FAILURE':
        response = {
            'state': task.state,
            'current': task.info.get('current', 0),
            'total': 100,
            'status': task.info.get('status', '')
        }
        if 'result' in task.info:
            response['result'] = task.info['result']
    else:
        # something went wrong in the background job
        response = {
            'state': task.state,
            'current': 1,
            'total': 100,
            'status': str(task.info),  # this is the exception raised
        }
    return json.htmlsafe_dumps(response)

@app.route("/")
def route():
    return redirect(url_for('home'))

@app.route("/home",methods = ["POST", "GET"])
def home():
    errors = {}
    if request.method == "POST":
        # get global variables
        search_input = request.form.get('file_path')
        search_csv = request.form.get('csv_folder')

        # error checking here
        if not os.path.isdir(search_input):
            errors['invalid_input_file_path'] = "File path entered is not valid"

        if not os.path.isdir(search_csv):
            errors['invalid_csv_file_path'] = "File path entered is not valid"

        # sys.stderr.write("errors:\n")
        # k = ["{}: {}".format(key, errors[key]) for key in errors.keys()]
        # sys.stderr.write(",".join(k))
        # sys.stderr.write("\n")
        # sys.stderr.write("search_input: {}\n".format(request.form.get('search_input')))
        # sys.stderr.write("add_job: {}\n".format(request.form.get('add_job')))

        if request.form.get('search_input') == 'Confirm':
            if len(errors) != 0:
                return render_template("home.html", input_folder=search_input, errors=errors, csv_folder=search_csv, search_csv=search_csv, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
            global input_filepath
            input_filepath = search_input

            global sample_csv
            sample_csv = search_csv

            # Save config if paths all work
            with open(os.path.dirname(os.path.realpath(__file__))+"/config.init", 'w') as c:
                c.write("{\n")
                c.write('\t"data-folder": "{}",\n'.format(search_input))
                c.write('\t"sample-barcode-csvs": "{}"'.format(search_csv))
                c.write('}\n')
        if request.form.get('add_job') == "Add Job":
            if len(errors) != 0:
                flash("WARNING:File paths entered are not valid")
                return render_template("home.html", input_folder=search_input, errors=errors, csv_folder=search_csv, search_csv=search_csv, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
            else:
                return redirect(url_for('parameters'))

    # return render_template("home.html", input_folder=input_filepath, csv_folder=sample_csv, eden_folder=schemes['eden_scheme'], eden_name=schemes['eden_scheme_name'], midnight_folder=schemes['midnight_scheme'], midnight_name=schemes['midnight_scheme_name'], artic_folder=schemes['artic_scheme'], artic_name=schemes['artic_scheme_name'])
    return render_template("home.html", input_folder=input_filepath, csv_folder=sample_csv, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

@app.route("/about")
def about():
	return render_template("about.html", VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

def check_special_characters(func):
    @functools.wraps(func)
    def wraper_check_char(*args, **kwargs):
        """
        check input args for special characters
        return error dic handled after call
        """
        def _detect_special_characer(pass_string, filename=False):
            if filename:
                regex= re.compile('^[a-zA-Z0-9._/-]+$')
            else:
                regex= re.compile('^[a-zA-Z0-9_/-]+$')
            if(regex.search(pass_string) == None):
                ret = True
            else:
                ret = False
            return ret
        # gets names of arguments
        args_name = inspect.getargspec(func)[0]
        # argnames: values into dic
        args_dict = dict(zip(args_name, args))

        errors = {}
        for arg in args_dict:
            a = args_dict[arg]
            if a:
                # sys.stderr.write(str(a))
                # sys.stderr.write("\n")
                if arg == "csv_filepath":
                    if _detect_special_characer(str(a), filename=True):
                        errors["char_error_{}".format(arg)] = "Invalid character in {}: ' {} ', please use: a-Z, 0-9 . _ /".format(arg, str(a))
                    continue
                if _detect_special_characer(str(a)):
                    errors["char_error_{}".format(arg)] = "Invalid character in {}: ' {} ', please use: a-Z, 0-9, _, /".format(arg, str(a))
        if len(errors) != 0:
            return errors, args[1]
        return func(*args, **kwargs)

    return wraper_check_char

@check_special_characters
def checkInputs(input_folder, output_folder, primer_scheme_dir, read_file, pipeline, override_data, min_length, max_length, job_name, output_input, csv_filepath, skip, num_samples):
    errors = {}

    #Check of jobname is used
    if qSys.getJobByName(job_name) is not None:
        errors['job_name'] = "Job Name has already been used."
        flash("Warning: Job Name has already been used.")
        return errors, output_folder

    if not input_folder:
        errors['input_folder'] = "Input Directory does not exist"
        flash("Warning: Input folder does not exist, please check input and try again")
        return errors, output_folder

    if num_samples == "multiple":
        if not os.path.isfile(csv_filepath):
            errors['csv_file'] = "csv file does not exist"
            flash("Warning: CSV file does not exist, please check input and try again")
            return errors, output_folder

    #give error if input folder path is empty
    if len(os.listdir(input_folder)) == 0:
        errors['input_folder'] = "Directory is empty."
        flash("Warning: Input folder contains no data, please check input and try again")
        return errors, output_folder

    io_check = output_input.strip("/")
    if_check = input_folder.strip("/")
    sys.stderr.write("io_check: {}\n".format(io_check))
    sys.stderr.write("if_check: {}\n".format(if_check))
    if io_check == if_check:
        errors['input_output_folder'] = "Output directory will be in the same folder as data"
        flash("Warning: Output directory will be in the same folder as data, please check data structure info in documentation.")
        return errors, output_folder

    #if no output folder entered, creates one inside of input folder
    if not output_folder and not os.path.isdir(output_input):
        errors['input_output_folder'] = "Input and output don't exist"
        flash("Warning: Input and output don't exist!")
        return errors, output_folder
    elif not output_folder and os.path.isdir(output_input):
        output_folder = output_input + "/output"
    elif output_folder and os.path.isdir(output_input):
        if output_folder[0] == "/":
            check_out = "/".join(output_folder.split("/")[:-1])
            if not os.path.isdir(check_out):
                errors['output_folder'] = "Parent directory of new output folder ( {} ) does not exist".format(check_out)
                flash("Warning: Parent directory of new output folder ( {} ) does not exist".format(check_out))
                return errors, output_folder
        else:
            output_folder = output_input + "/" + output_folder
    else:
        errors['input_folder'] = "Input folder does not exist, pleas check: {}".format(output_input)
        flash("Warning: Input folder does not exist, pleas check: {}".format(output_input))
        return errors, output_folder

    if output_folder[-1] == "/":
        output_folder = output_folder[:-1]

    if primer_scheme_dir[-1] == "/":
        primer_scheme_dir = primer_scheme_dir[:-1]

    #give error if primer schemes folder path is invalid or empty
    if not os.path.isdir(primer_scheme_dir):
        errors['primer_scheme_dir'] = "Invalid path."
        flash("Warning: primer_scheme_dir does not exist, pleas check: {}".format(primer_scheme_dir))
        return errors, output_folder
    elif len(os.listdir(primer_scheme_dir)) == 0:
        errors['primer_scheme_dir'] = "Directory is empty."
        flash("Warning: Primer_scheme_dir is empty, pleas check: {}".format(primer_scheme_dir))
        return errors, output_folder


    #if read file is specified by user
    if read_file:
        if not os.path.isfile(read_file):
            errors['read_file'] = "Invalid path/file."
    else:
        #to be filled later
        read_file = ""

    if pipeline in ["both", "nanopolish"]:
        # check for sequencing summary file for nanopolish
        seq_sum_found = False
        for file in os.listdir(input_folder):
            if fnmatch.fnmatch(file, "*sequencing_summary*.txt"):
                seq_sum_found = True
        if not seq_sum_found:
            flash("Warning: sequencing_summary.txt file not found in input folder structure")
            errors['input_folder'] = "sequencing_summary.txt file not found"
            return errors, output_folder

    #both pipelines running
    if pipeline == "both":
        # TODO: check all os.system() calls
        if not os.path.exists(output_folder):
            make_dir = 'mkdir ' + output_folder
            if os.system(make_dir) != 0:
                errors['mkdir'] = "Failed to create output directory, please check parent path exists and has write permission"
                flash("Warning: Failed to create output directory, please check parent path exists and has write permission")
                return errors, output_folder
        if override_data is True and os.path.exists(output_folder):
            # remove = "rm -r " + output_folder + "/all_cmds_log.txt"
            remove = "rm -r " + output_folder
            if os.system(remove) !=0:
                errors['remove_folder'] = "Could not detele output_directory"
                flash("Warning: Could not delete {}".format(output_folder))
                return errors, output_folder
            make_dir = 'mkdir ' + output_folder
            if os.system(make_dir) != 0:
                errors['mkdir'] = "Failed to create output directory, please check parent path exists and has write permission"
                flash("Warning: Failed to create output directory, please check parent path exists and has write permission")
                return errors, output_folder
        elif check_override(output_folder, override_data, skip) and os.path.exists(output_input):
            errors['override'] = True
            flash("Warning: Output folder is NOT empty. Please choose another folder or delete/move files in it.")
            return errors, output_folder
        #if the output folder does not exist, it is created
        if not os.path.exists(output_folder + "/medaka"):
            # make_dir = 'mkdir ' + output_folder
            # if os.system(make_dir) != 0:
            #     errors['mkdir_m1'] = "Failed to create output directory, please check parent path exists and has write permission"
            #     flash("Warning: Could not mkdir {}".format(output_folder))
            #     return errors, output_folder
            make_dir_m = 'mkdir ' + output_folder + '/medaka'
            if os.system(make_dir_m) != 0:
                errors['mkdir_m2'] = "Failed to create medaka directory, please check parent path exists and has write permission"
                flash("Warning: Could not mkdir {}/medaka".format(output_folder))
                return errors, output_folder
        #if the output folder does not exist, it is created
        if not os.path.exists(output_folder + "/nanopolish"):
            # make_dir = 'mkdir ' + output_folder
            # # os.system(make_dir)
            # if os.system(make_dir) != 0:
            #     errors['mkdir_n1'] = "Failed to create output directory, please check parent path exists and has write permission"
            #     flash("Warning: Could not mkdir {}".format(output_folder))
            #     return errors, output_folder
            make_dir_n = 'mkdir ' + output_folder + '/nanopolish'
            if os.system(make_dir_n) != 0:
                errors['mkdir_n2'] = "Failed to create nanopolish directory, please check parent path exists and has write permission"
                flash("Warning: Could not mkdir {}/nanopolish".format(output_folder))
                return errors, output_folder

        if check_override(output_folder + "/medaka", override_data, skip) and os.path.exists(output_input):
            flash("Warning: Output folder is NOT empty. Please choose another folder or delete/move files in it.")
            errors['override'] = True
            return errors, output_folder

        if check_override(output_folder + "/nanopolish", override_data, skip) and os.path.exists(output_input):
            flash("Warning: Output folder is NOT empty. Please choose another folder or delete/move files in it.")
            errors['override'] = True
            return errors, output_folder

        # Make empty log file for initial progress rendering
        make_log_m = 'touch \"' + output_folder + '\"/medaka/all_cmds_log.txt'
        make_log_n = 'touch \"' + output_folder + '\"/nanopolish/all_cmds_log.txt'
        if os.system(make_log_m) != 0:
            errors['touch_m'] = "Failed to write to output directory, please check path exists and has write permission"
            flash("Warning: Failed to write to output directory, please check path exists and has write permission")
            return errors, output_folder
        if os.system(make_log_n) != 0:
            errors['touch_n'] = "Failed to write to output directory, please check path exists and has write permission"
            flash("Warning: Failed to write to output directory, please check path exists and has write permission")
            return errors, output_folder
    else:
        #TODO: if not "both" still make the folders medaka | nanopolish based on selection
        #if the output folder does not exist, it is created
        if not os.path.exists(output_folder):
            make_dir = 'mkdir ' + output_folder
            if os.system(make_dir) != 0:
                errors['mkdir'] = "Failed to create output directory, please check parent path exists and has write permission"
                flash("Warning: Failed to create output directory, please check parent path exists and has write permission")
                return errors, output_folder

        if override_data is True:
            # remove = "rm -r " + output_folder + "/all_cmds_log.txt"
            remove = "rm -r " + output_folder
            if os.system(remove) !=0:
                errors['remove_folder'] = "Could not detele output_directory"
                flash("Warning: Could not delete {}".format(output_folder))
                return errors, output_folder
            make_dir = 'mkdir ' + output_folder
            if os.system(make_dir) != 0:
                errors['mkdir'] = "Failed to create output directory, please check parent path exists and has write permission"
                flash("Warning: Failed to create output directory, please check parent path exists and has write permission")
                return errors, output_folder
        elif check_override(output_folder, override_data, skip) and os.path.exists(output_input):
            errors['override'] = True
            flash("Warning: Output folder is NOT empty. Please choose another folder or delete/move files in it.")
            return errors, output_folder
        # Make empty log file for initial progress rendering
        make_log = 'touch \"' + output_folder + '\"/all_cmds_log.txt'
        if os.system(make_log) != 0:
            errors['touch'] = "Failed to write to output directory, please check path exists and has write permission"
            flash("Warning: Failed to create log file, please check parent path exists and has write permission")
            return errors, output_folder



    #check length parameters are valid
    if min_length.isdigit() == False:
        errors['invalid_length'] = "Invalid minimum length."
        if max_length.isdigit() == False:
            errors['invalid_length'] = "Invalid maximum and minimum length."
    elif max_length.isdigit() == False:
        errors['invalid_length'] = "Invalid maximum length."
    elif int(max_length) < int(min_length):
        errors['invalid_length'] = "Invalid parameters: Maximum length smaller than minimum length."

    return errors, output_folder

def getInputFolders(filepath):
    # find all the current input folders
    checkFoldersCmd = "cd && cd " + filepath + " && ls"
    print("check folders command")
    print(checkFoldersCmd)


    folders = subprocess.check_output(checkFoldersCmd, shell=True, stderr=subprocess.STDOUT).decode("ascii").split("\n")

    return folders

@app.route("/parameters", methods = ["POST","GET"])
def parameters():
    # get global variables for use
    global input_filepath
    global sample_csv
    global schemes

    # get a list of all the folders in the input and csv folders to be displayed to the user

    folders = getInputFolders(input_filepath)
    csvs = getInputFolders(sample_csv)

    if request.method == "POST":
        # get curr queue
        queueList = []
        if not qSys.queue.empty():
            for item in qSys.queue.getItems():
                queueList.append({item._job_name : url_for('progress', job_name=item._job_name, task_id = item._task_id)})

        queueDict = {'jobs': queueList}
        displayQueue = json.htmlsafe_dumps(queueDict)

        #get parameters
        job_name = request.form.get('job_name')
        input_folder = request.form.get('input_folder')
        read_file = request.form.get('read_file')
        primer_scheme_dir = request.form.get('primer_scheme_dir')
        primer_scheme = request.form.get('primer_scheme')
        primer_type = request.form.get('primer_type')
        other_primer_type = request.form.get('other_primer_type')
        output_folder = request.form.get('output_folder')
        normalise = request.form.get('normalise')
        num_threads = request.form.get('num_threads')
        pipeline = request.form.get('pipeline')
        num_samples = request.form.get('num_samples')
        min_length = request.form.get('min_length')
        max_length = request.form.get('max_length')
        bwa = request.form.get('bwa')
        skip_nanopolish = request.form.get('skip_nanopolish')
        dry_run = request.form.get('dry_run')
        # num_samples = request.form.get('num_samples')
        barcode_type = request.form.get('barcode_type')
        csv_file = request.form.get('csv_file')
        virus = request.form.get('virus')
        override_data = request.form.get('override_data')
        # DEBUG
        step = int(request.form.get('step'))

        sys.stderr.write("override_data: {}\n".format(override_data))

        # set correct primer_type - if primer type is other, get the correct primer type from the tet input
        # primer_select is so that on reload, the correct radio button will be selected
        primer_select = primer_type

        if virus == 'custom':
            if other_primer_type:
                primer_type = other_primer_type
            else:
                primer_type = "Custom-primer-scheme"


        # store input_name
        input_name = input_folder

        #csv filepath
        csv_filepath = sample_csv + '/' + csv_file

        # concat /data to input folder
        # global input_filepath
        input_folder = input_filepath + '/' + input_folder
        filename = os.path.dirname(os.path.realpath(__file__))
        # if no output folder entered, creates one inside of input folder
        # Do this to put output above input folder to stop fastq cross talk
        # if not output_folder:
        #     output_folder = input_folder + "/output"
        # else:
        #     if output_folder[0] != "/":
        #         output_folder = input_folder + output_folder
        if not os.path.isdir(input_folder):
            input_folder = ""
            output_input = ""
        else:
            os.chdir(input_folder)
            tmp_oi = os.getcwd()
            output_input = tmp_oi

            # get the correct input folder filepath from user input
            # path = glob.glob(input_folder + '/*/*')[0]
            # use fnmatch with walk to get fastq_pass, fastq_fail folders
            # then split off the last bit to get the top folder for the gather command
            tmp_folder_list = []
            for dName, sdName, fList in os.walk(input_folder):
                for fileName in sdName:
                    if fnmatch.fnmatch(fileName, "fastq*"):
                        tmp_folder_list.append(os.path.join(dName, fileName))
            if len(tmp_folder_list) == 0:
                queueList = []
                flash("Warning: No fastq files found in {}".format(input_folder))
                errors = {}
                if qSys.queue.empty():
                    return render_template("parameters.html", job_name=job_name, queue=None,
                                            input_name=input_name, input_folder=input_folder,
                                            output_folder=output_folder, virus=virus,
                                            pipeline=pipeline, min_length=min_length,
                                            max_length=max_length, primer_scheme=primer_scheme,
                                            primer_type=primer_type, num_samples=num_samples,
                                            primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
                                            errors=errors, folders=folders, csvs=csvs, csv_name=csv_file,
                                            other_primer_type=other_primer_type, primer_select=primer_select,
                                            schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

                return render_template("parameters.html", job_name=job_name, queue=displayQueue,
                                        input_name=input_name, input_folder=input_folder,
                                        output_folder=output_folder, virus=virus,
                                        pipeline=pipeline, min_length=min_length,
                                        max_length=max_length, primer_scheme=primer_scheme,
                                        primer_type=primer_type, num_samples=num_samples,
                                        primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
                                        errors=errors,folders=folders, csvs=csvs, csv_name=csv_file,
                                        other_primer_type=other_primer_type, primer_select=primer_select,
                                        schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
            tmp_path = tmp_folder_list[0].split("/")[:-1]
            path = "/".join(tmp_path)
            os.chdir(path)
            input_folder = os.getcwd()

        #if user agrees output can override files with the same name in output folder
        if request.form.get('override_data'):
            override_data = True
        else:
            override_data = False

        # check errors
        errors = {}
        errors, output_folder_checked = checkInputs(input_folder, output_folder, primer_scheme_dir,
                                                    read_file, pipeline, override_data, min_length,
                                                    max_length, job_name, output_input, csv_filepath, step, num_samples)

        # if an output folder does not exist, make one
        # if not output_folder:
        #     output_folder = output_folder_checked

        output_folder = output_folder_checked

        # validate csv contents.
        # No special characters -
        # comma separated -
        # 2 columns -
        # 2nd column should have NB or RB or BC-
        def _detect_special(pass_string):
            regex= re.compile('^[a-zA-Z0-9,_-]+$')
            if(regex.search(pass_string) == None):
                ret = True
            else:
                ret = False
            return ret
        sys.stderr.write("checking CSV file: {}\n".format(csv_filepath))
        if os.path.isfile(csv_filepath):
            sys.stderr.write("csv file exists\n")
            with open(csv_filepath, 'r') as c:
                for l in c:
                    l = l.strip("\n")
                    if _detect_special(l):
                        flash("Warning: csv file malformed: special characters detected ")
                        errors['csv_malformed'] = "csv is malformed, special characters detected a-zA-Z0-9,_- only"
                        break
                    l = l.split(",")
                    if len(l) != 2:
                        errors['csv_malformed'] = "csv is malformed, more or less than 2 columns"
                        flash("Warning: csv file malformed: more or less than 2 columns")
                        break
                    else:
                        if l[1][:2] not in ["NB", "RB", "BC"]:
                            errors['csv_malformed'] = "csv is malformed, not NB or RB or BC for barcode"
                            flash("Warning: csv file malformed: not NB or RB or BC for barcode")
                            break

        sys.stderr.write("printing errors:\n")
        k = ["{}: {}".format(key, errors[key]) for key in errors.keys()]
        sys.stderr.write(",".join(k))
        sys.stderr.write("\n")
        # if queue is full, add an error to the list
        if qSys.queue.full():
            errors['full_queue'] = "Job queue is full."

        # display errors if errors exist
        if len(errors) != 0:

            # k = ["{}: {}".format(key, errors[key]) for key in errors.keys()]
            # sys.stderr.write(",".join(k))
            # sys.stderr.write("\n")
            #Update displayed queue on home page
            queueList = []

            if qSys.queue.empty():
                return render_template("parameters.html", job_name=job_name, queue=None,
                                        input_name=input_name, input_folder=input_folder,
                                        output_folder=output_folder, virus=virus,
                                        pipeline=pipeline, min_length=min_length,
                                        max_length=max_length, primer_scheme=primer_scheme,
                                        primer_type=primer_type, num_samples=num_samples,
                                        primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
                                        errors=errors, folders=folders, csvs=csvs, csv_name=csv_file,
                                        other_primer_type=other_primer_type, primer_select=primer_select,
                                        schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

            return render_template("parameters.html", job_name=job_name, queue=displayQueue,
                                    input_name=input_name, input_folder=input_folder,
                                    output_folder=output_folder, virus=virus,
                                    pipeline=pipeline, min_length=min_length,
                                    max_length=max_length, primer_scheme=primer_scheme,
                                    primer_type=primer_type, num_samples=num_samples,
                                    primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
                                    errors=errors,folders=folders, csvs=csvs, csv_name=csv_file,
                                    other_primer_type=other_primer_type, primer_select=primer_select,
                                    schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)


        #no spaces in the job name - messes up commands
        job_name = job_name.replace(" ", "_")

        # create new jobs
        if pipeline != "both":
            #Create a new instance of the Job class
            new_job = qSys.newJob(job_name, input_folder, read_file, primer_scheme_dir, primer_scheme, primer_type, output_folder, normalise, num_threads, pipeline, min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples,barcode_type, input_name, csv_filepath, primer_select, input_name)

            #Add job to queue
            qSys.addJob(new_job)
            print("qSys has jobs: ", qSys.printQueue())
            new_task = executeJob.apply_async(args=[new_job.job_name, new_job.gather_cmd, new_job.demult_cmd, new_job.min_cmd, new_job.plot_cmd, step])
            new_job.task_id = new_task.id
        #if both pipelines
        else:
            #Create a new medaka instance of the Job class
            new_job_m = qSys.newJob(job_name + "_medaka", input_folder, read_file, primer_scheme_dir, primer_scheme, primer_type, output_folder + "/medaka", normalise, num_threads, "medaka", min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples,barcode_type, input_name, csv_filepath, primer_select, input_name)
            #Create a new nanopolish instance of the Job class
            new_job_n = qSys.newJob(job_name + "_nanopolish", input_folder, read_file, primer_scheme_dir, primer_scheme, primer_type, output_folder + "/nanopolish", normalise, num_threads, "nanopolish", min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples,barcode_type, input_name, csv_filepath, primer_select, input_name)

            #Add medaka job to queue
            qSys.addJob(new_job_m)
            task_m = executeJob.apply_async(args=[new_job_m.job_name, new_job_m.gather_cmd, new_job_m.demult_cmd, new_job_m.min_cmd, new_job_m.plot_cmd, step])
            new_job_m.task_id = task_m.id
            #Add nanopolish job to queue
            qSys.addJob(new_job_n)
            task_n = executeJob.apply_async(args=[new_job_n.job_name, new_job_n.gather_cmd, new_job_n.demult_cmd, new_job_n.min_cmd, new_job_n.plot_cmd, step])
            new_job_n.task_id = task_n.id

        # redirect to the progress page
        if pipeline == "both":
            return redirect(url_for('progress', job_name=job_name+"_medaka"))
        else:
            return redirect(url_for('progress', job_name=job_name))

    #Update displayed queue on home page
    queueList = []
    if qSys.queue.empty():
        return render_template("parameters.html", queue=None, folders=folders, csvs=csvs, schemes=schemes, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

    for item in qSys.queue.getItems():
        queueList.append({item._job_name : url_for('progress', job_name=item._job_name, task_id = item._task_id)})

    queueDict = {'jobs': queueList}
    displayQueue = json.htmlsafe_dumps(queueDict)
    return render_template("parameters.html", queue = displayQueue, folders=folders, csvs=csvs, schemes=schemes, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

# error page, accessed if a user wants to re-run a job if an error occurs during a run
# @app.route("/error/<job_name>", methods = ["POST","GET"])
# def error(job_name):
#     # get the job that needs to be re-run
#     job = qSys.getJobByName(job_name)
#
#     # get global variables
#     global input_filepath
#     global sample_csv
#     folders = getInputFolders(input_filepath)
#     csvs = getInputFolders(sample_csv)
#
#     # if the job exists, get all the parameters used in the initial run so that they can be rendered for the user
#     if job != None:
#         input_folder = job.input_folder
#         input_name = job.input_name
#         output_folder = job.output_folder
#         read_file = job.read_file
#         pipeline = job.pipeline
#         min_length = job.min_length
#         max_length = job.max_length
#         primer_select = job.primer_select
#         primer_scheme = job.primer_scheme
#         primer_scheme_dir = job.primer_scheme_dir
#         primer_type = job.primer_type
#         num_samples = job.num_samples
#         barcode_type = job.barcode_type
#         # abort existing job
#         task = job.task_id
#         blank = killJob.apply_async(args=[job_name])
#         celery.control.revoke(task, terminate=True, signal='SIGKILL')
#         qSys.removeQueuedJob(job_name)
#
#     if request.method == "POST":
#         #get parameters
#         job_name = request.form.get('job_name')
#         input_folder = request.form.get('input_folder')
#         read_file = request.form.get('read_file')
#         primer_scheme_dir = request.form.get('primer_scheme_dir')
#         primer_scheme = request.form.get('primer_scheme')
#         primer_type = request.form.get('primer_type')
#         other_primer_type = request.form.get('other_primer_type')
#         output_folder = request.form.get('output_folder')
#         normalise = request.form.get('normalise')
#         num_threads = request.form.get('num_threads')
#         pipeline = request.form.get('pipeline')
#         num_samples = request.form.get('num_samples')
#         min_length = request.form.get('min_length')
#         max_length = request.form.get('max_length')
#         bwa = request.form.get('bwa')
#         skip_nanopolish = request.form.get('skip_nanopolish')
#         dry_run = request.form.get('dry_run')
#         # num_samples = request.form.get('num_samples')
#         barcode_type = request.form.get('barcode_type')
#         csv_file = request.form.get('csv_file')
#         virus = request.form.get('virus')
#         override_data = request.form.get('override_data')
#         step = int(request.form.get('step'))
#
#         # set correct primer_type - if primer type is other, get the correct primer type from the tet input
#         # primer_select is so that on reload, the correct radio button will be selected
#         primer_select = primer_type
#
#         if virus == 'custom':
#             if other_primer_type:
#                 primer_type = other_primer_type
#             else:
#                 primer_type = "Custom-primer-scheme"
#
#
#         # store input_name
#         input_name = input_folder
#
#         #csv filepath
#         csv_filepath = sample_csv + '/' + csv_file
#
#         # concat /data to input folder
#         input_folder = input_filepath + '/' + input_folder
#         filename = os.path.dirname(os.path.realpath(__file__))
#         if not os.path.isdir(input_folder):
#             input_folder = ""
#             output_input = ""
#         else:
#             os.chdir(input_folder)
#             tmp_oi = os.getcwd()
#             output_input = tmp_oi
#
#             # get the correct input folder filepath from user input
#             # path = glob.glob(input_folder + '/*/*')[0]
#             # use fnmatch with walk to get fastq_pass, fastq_fail folders
#             # then split off the last bit to get the top folder for the gather command
#             tmp_folder_list = []
#             for dName, sdName, fList in os.walk(input_folder):
#                 for fileName in sdName:
#                     if fnmatch.fnmatch(fileName, "fastq*"):
#                         tmp_folder_list.append(os.path.join(dName, fileName))
#             tmp_path = tmp_folder_list[0].split("/")[:-1]
#             path = "/".join(tmp_path)
#             os.chdir(path)
#             input_folder = os.getcwd()
#
#         #if user agrees output can override files with the same name in output folder
#         if request.form.get('override_data'):
#             override_data = True
#         else:
#             override_data = False
#
#         # check errors
#         errors = {}
#         errors, output_folder_checked = checkInputs(input_folder, output_folder, primer_scheme_dir,
#                                                     read_file, pipeline, override_data, min_length,
#                                                     max_length, job_name, output_input, csv_filepath, step, num_samples)
#
#         # if an output folder does not exist, make one
#         # if not output_folder:
#         #     output_folder = output_folder_checked
#
#         output_folder = output_folder_checked
#
#         # if queue is full, add an error to the list
#         if qSys.queue.full():
#             errors['full_queue'] = "Job queue is full."
#
#         # display errors if errors exist
#         if len(errors) != 0:
#             #Update displayed queue on home page
#             queueList = []
#             if qSys.queue.empty():
#                 return render_template("parameters.html", job_name=job_name, queue=None,
#                                         input_name=input_name, input_folder=input_folder,
#                                         output_folder=output_folder, virus=virus,
#                                         pipeline=pipeline, min_length=min_length,
#                                         max_length=max_length, primer_scheme=primer_scheme,
#                                         primer_type=primer_type, num_samples=num_samples,
#                                         primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
#                                         errors=errors, folders=folders, csvs=csvs, csv_name=csv_file,
#                                         other_primer_type=other_primer_type, primer_select=primer_select,
#                                         schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
#             for item in qSys.queue.getItems():
#                 queueList.append({item.job_name : url_for('progress', job_name=item.job_name, task_id = item.task_id)})
#
#             queueDict = {'jobs': queueList}
#             displayQueue = json.htmlsafe_dumps(queueDict)
#
#             return render_template("parameters.html", job_name=job_name, queue=None,
#                                     input_name=input_name, input_folder=input_folder,
#                                     output_folder=output_folder, virus=virus,
#                                     pipeline=pipeline, min_length=min_length,
#                                     max_length=max_length, primer_scheme=primer_scheme,
#                                     primer_type=primer_type, num_samples=num_samples,
#                                     primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
#                                     errors=errors, folders=folders, csvs=csvs, csv_name=csv_file,
#                                     other_primer_type=other_primer_type, primer_select=primer_select,
#                                     schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
#
#         #no spaces in the job name - messes up commands
#         job_name = job_name.replace(" ", "_")
#
#         # create new jobs
#         if pipeline != "both":
#             #Create a new instance of the Job class
#             new_job = qSys.newJob(job_name, input_folder, read_file, primer_scheme_dir, primer_scheme, primer_type, output_folder, normalise, num_threads, pipeline, min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples, barcode_type, input_name, csv_filepath, primer_select, input_name)
#
#             #Add job to queue
#             qSys.addJob(new_job)
#             print("qSys has jobs: ", qSys.printQueue())
#             new_task = executeJob.apply_async(args=[new_job.job_name, new_job.gather_cmd, new_job.demult_cmd, new_job.min_cmd, new_job.plot_cmd, step])
#             new_job.task_id = new_task.id
#
#         #if both pipelines
#         else:
#             #Create a new medaka instance of the Job class
#             new_job_m = qSys.newJob(job_name + "_medaka", input_folder, read_file, primer_scheme_dir, primer_scheme, primer_type, output_folder + "/medaka", normalise, num_threads, "medaka", min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples,barcode_type, input_name, csv_filepath, primer_select, input_name)
#             #Create a new nanopolish instance of the Job class
#             new_job_n = qSys.newJob(job_name + "_nanopolish", input_folder, read_file, primer_scheme_dir, primer_scheme, primer_type, output_folder + "/nanopolish", normalise, num_threads, "nanopolish", min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples,barcode_type, input_name, csv_filepath, primer_select, input_name)
#
#             #Add medaka job to queue
#             qSys.addJob(new_job_m)
#             task_m = executeJob.apply_async(args=[new_job_m.job_name, new_job_m.gather_cmd, new_job_m.demult_cmd, new_job_m.min_cmd, new_job_m.plot_cmd, step])
#             new_job_m.task_id = task_m.id
#             #Add nanopolish job to queue
#             qSys.addJob(new_job_n)
#             task_n = executeJob.apply_async(args=[new_job_n.job_name, new_job_n.gather_cmd, new_job_n.demult_cmd, new_job_n.min_cmd, new_job_n.plot_cmd, step])
#             new_job_n.task_id = task_n.id
#         if pipeline == "both":
#             return redirect(url_for('progress', job_name=job_name+"_medaka"))
#         else:
#             return redirect(url_for('progress', job_name=job_name))
#
#     #Update displayed queue on home page
#     queueList = []
#     if qSys.queue.empty():
#         return render_template("parameters.html", job_name=job_name, queue=None,
#                                 input_name=input_name, input_folder=input_folder,
#                                 output_folder=output_folder, virus=virus,
#                                 pipeline=pipeline, min_length=min_length,
#                                 max_length=max_length, primer_scheme=primer_scheme,
#                                 primer_type=primer_type, num_samples=num_samples,
#                                 primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
#                                 errors=errors, folders=folders, csvs=csvs, csv_name=csv_file,
#                                 other_primer_type=other_primer_type, primer_select=primer_select,
#                                 schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
#
#     for item in qSys.queue.getItems():
#         queueList.append({item.job_name : url_for('progress', job_name=item.job_name, task_id = item.task_id)})
#
#     queueDict = {'jobs': queueList}
#     displayQueue = json.htmlsafe_dumps(queueDict)
#     return render_template("parameters.html", job_name=job_name, queue=None,
#                             input_name=input_name, input_folder=input_folder,
#                             output_folder=output_folder, virus=virus,
#                             pipeline=pipeline, min_length=min_length,
#                             max_length=max_length, primer_scheme=primer_scheme,
#                             primer_type=primer_type, num_samples=num_samples,
#                             primer_scheme_dir=primer_scheme_dir, barcode_type=barcode_type,
#                             errors=errors, folders=folders, csvs=csvs, csv_name=csv_file,
#                             other_primer_type=other_primer_type, primer_select=primer_select,
#                             schemes=schemes, override_data=override_data, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

# Progress page
@app.route("/progress/<job_name>", methods = ["GET", "POST"])
def progress(job_name):

    # get the job
    job = qSys.getJobByName(job_name)

    # get the filepath where the output is located
    path = job.output_folder
    path +="/all_cmds_log.txt"

    ################## TODO: NEED TO CHANGE
    with open(path, "r") as f:
        outputLog = f.read().replace("\n","<br/>")

    # find the status of the current job
    if re.findall(r':D', outputLog):
        frac = "4"
    elif len(re.findall(r'COMPLETE', outputLog)) == 1:
        frac = "1"
    elif len(re.findall(r'COMPLETE', outputLog)) == 2:
        frac = "2"
    elif len(re.findall(r'COMPLETE', outputLog)) > 2:
        frac = "3"
    else:
        frac = "0"

    # find any errors that occur in the output log
    pattern = "<br\/>[A-Za-z0-9\s]*ERROR"
    numErrors = len(re.findall(pattern, outputLog, re.IGNORECASE)) + len(re.findall(r'No such file or directory', outputLog, re.IGNORECASE))

    # get all the parameters in the job so that they can be displayed for the user
    num_in_queue = qSys.queue.getJobNumber(job_name)
    queue_length = qSys.queue.getNumberInQueue()
    input_folder = job.input_folder
    output_folder = job.output_folder
    read_file = job.read_file
    pipeline = job.pipeline
    min_length = job.min_length
    max_length = job.max_length
    primer_scheme = job.primer_scheme
    primer_type = job.primer_type
    num_samples = job.num_samples
    barcode_type = job.barcode_type

    return render_template("progress.html", outputLog=outputLog, num_in_queue=num_in_queue,
                            queue_length=queue_length, job_name=job_name, frac=frac, input_folder=input_folder, output_folder=output_folder,
                            read_file=read_file, pipeline=pipeline, min_length=min_length, max_length=max_length, primer_scheme=primer_scheme,
                            primer_type=primer_type, num_samples=num_samples,barcode_type=barcode_type,numErrors=numErrors, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

@app.route("/abort/<job_name>", methods = ["GET", "POST"])
def abort(job_name):
    job = qSys.getJobByName(job_name)
    task = job.task_id
    blank = killJob.apply_async(args=[job_name])
    celery.control.revoke(task,terminate=True, signal='SIGKILL')

    qSys.removeQueuedJob(job_name)
    return redirect(url_for("home"))

@app.route("/abort/delete/<job_name>", methods = ["GET", "POST"])
def abort_delete(job_name):
    job = qSys.getJobByName(job_name)
    task = job.task_id
    blank = killJob.apply_async(args=[job_name])
    celery.control.revoke(task,terminate=True, signal='SIGKILL')
    os.system('rm -r ' + job.output_folder)

    qSys.removeQueuedJob(job_name)
    return redirect(url_for("home"))

@app.route("/delete/<job_name>", methods = ["GET", "POST"])
def delete(job_name):
    images = os.path.dirname(os.path.realpath(__file__)) + '/static/tmp_plots/' + job_name
    print(images)
    os.system('rm -r ' + images + '*' )
    qSys.removeCompletedJob(job_name)
    return redirect(url_for("home"))

@app.route("/output/<job_name>", methods = ["GET", "POST"])
def output(job_name):
    job = qSys.getJobByName(job_name)
    output_folder = job.output_folder

    # debugging
    # output_folder = "/home/jamfer/data/SARS-CoV-2/test_interARTIC/FLFL031920/dfsdfsdfsdf"

    sample_folders = []
    plots = {}
    vcfs = {}
    fastas = {}
    plots_found = False
    vcf_found = False
    fasta_found = False
    sample = ""

    if output_folder:
        # sys.stderr.write("output_folder found\n")
        if os.path.exists(output_folder):
            #Finds all files in the output folder
            for (dirpath, dirnames, filenames) in os.walk(output_folder):
                for i in dirnames:
                    if "_medaka" in i:
                        sample_folders.append(i)
                    elif "_nanopolish" in i:
                        sample_folders.append(i)
                for name in filenames:
                    #finds barplot pngs
                    if fnmatch.fnmatch(name, '*CoVarPlot.png'):
                        sample_name = dirpath.split("/")[-1]
                        plots[sample_name] = os.path.join(dirpath,name)
                        plots_found = True
                    #finds vcf files
                    if fnmatch.fnmatch(name, '*.pass.vcf.gz'):
                        sample_name = dirpath.split("/")[-1]
                        vcfs[sample_name] = (os.path.join(dirpath,name))
                        vcf_found = True
                    #finds consensus.fasta
                    if fnmatch.fnmatch(name, '*.consensus.fasta'):
                        sample_name = dirpath.split("/")[-1]
                        fastas[sample_name] = os.path.join(dirpath,name)
                        fasta_found = True
        sample_folders.sort(key=lambda s: list(map(str, s.split('_')))[-2])

    if request.method == "POST":
        sample = request.form.get('sample_folder')
        # sys.stderr.write("sample:{}\n".format(sample))
        if vcf_found:
            if sample in vcfs.keys():
                # sys.stderr.write("vcf found and building\n")
                # try:
                header = []
                vcf_table = []
                with gzip.open(vcfs[sample], "rt") as f:
                    for l in f:
                        if l[:2] == "##":
                            continue
                        if l[0] == "#":
                            l = l[1:].strip('\n')
                            # sys.stderr.write("header = {}\n".format(l))
                            l = l.split('\t')
                            header = l
                            vcf_table.append(["CHROM", "POS", "REF", "ALT", "QUAL", "FILTER", "DEPTH"])
                            continue
                        l = l.strip('\n')
                        l = l.split('\t')
                        row = dict(zip(header, l))
                        # k = ["{}: {}".format(key, row[key]) for key in row.keys()]
                        # sys.stderr.write(",".join(k))
                        # sys.stderr.write("\n")
                        depth = int(row["INFO"].split(";")[0].split("=")[1])
                        vcf_table.append([row["CHROM"], int(row["POS"]), row["REF"], row["ALT"], float(row["QUAL"]), row["FILTER"], depth])

                df = pd.DataFrame(vcf_table[1:], columns=vcf_table[0])
                vcf_table_html = df.to_html(classes='mystyle')

                # sys.stderr.write("vcf built\n")
                # except:
                #     flash("Warning: vcf table creation failed for {}".format(sample))
                #     sys.stderr.write("vcf failed to build\n")
                #     vcf_table = False
            else:
                flash("Warning: No vcf files found in {}".format(output_folder))
                sys.stderr.write("no vcf for sample found\n")
                return render_template("output.html", job_name=job_name, sample_folders=sample_folders, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
        else:
            flash("Warning: No vcf files found in {}".format(output_folder))
            sys.stderr.write("no vcfs found\n")
            return render_template("output.html", job_name=job_name, sample_folders=sample_folders, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
        if plots_found:
            if sample in plots.keys():
                plot = plots[sample]
                plot_file = plot.split("/")[-1]
                plot_path = os.path.dirname(os.path.realpath(__file__)) + '/static/tmp_plots/' + job_name
                if not os.path.isdir(plot_path):
                    mkdir = "mkdir -p " + plot_path
                    os.system(mkdir)
                cp_plot = "cp " + plot + " " + plot_path
                os.system(cp_plot)
                html_plot = "/static/tmp_plots/" + job_name+ "/" + plot_file
                # sys.stderr.write("plots found: {}\n".format("/static/tmp_plots/"+job_name+ "/" + plot_file))
            else:
                plot = False
                sys.stderr.write("plot for sample not found in plots\n")
                return render_template("output.html", job_name=job_name, sample_folders=sample_folders, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
        else:
            plot = False
            sys.stderr.write("plots not found\n")
            return render_template("output.html", job_name=job_name, sample_folders=sample_folders, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

        if fasta_found:
            if sample in fastas.keys():
                fasta = fastas[sample]
                fasta_file = fasta.split("/")[-1]
                fasta_path = os.path.dirname(os.path.realpath(__file__)) + '/static/tmp_fastas/' + job_name
                if not os.path.isdir(fasta_path):
                    mkdir = "mkdir -p " + fasta_path
                    os.system(mkdir)
                cp_fasta = "cp " + fasta + " " + fasta_path
                os.system(cp_fasta)
                html_fasta = "/static/tmp_fastas/" + job_name+ "/" + fasta_file
            else:
                flash("Warning: No fasta files found in {}".format(output_folder))
                sys.stderr.write("no fasta for sample found\n")
                return render_template("output.html", job_name=job_name, sample_folders=sample_folders, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
        else:
            flash("Warning: No fasta files found in {}".format(output_folder))
            sys.stderr.write("no vcfs found\n")
            return render_template("output.html", job_name=job_name, sample_folders=sample_folders, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)


        # sys.stderr.write("running plot return\n")
        return render_template("output.html", job_name=job_name, output_folder=output_folder, vcf_table=vcf_table_html, plot=html_plot, fasta=html_fasta, plots_found=plots_found, vcf_found=vcf_found, fasta_found=fasta_found, sample_folders=sample_folders, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)
    # sys.stderr.write("running regular return\n")
    return render_template("output.html", job_name=job_name, sample_folders=sample_folders, sample_folder=sample, VERSION=VERSION, ARTIC_VERSION=ARTIC_VERSION)

    # return render_template("output.html", job_name=job_name, output_folder=output_folder, output_files=output_files, save_graphs=save_able, vcf_table=vcf_table, create_vcfs=create_able, plots_found=plots_found, vcf_found=vcf_found)


if __name__ == "__main__":
    # app.run(debug=True)
    """
    ---------------------------------------------------------------------------
       Arguments
    ---------------------------------------------------------------------------
    """

    parser = MyParser(
        description="interARTIC - coronavirus genome analysis web app")
    parser.add_argument("redis_port", nargs='?',
                        help="redis port *pass_through**")
    parser.add_argument("-a", "--web_address", default="127.0.0.1",
                        help="localhost default 127.0.0.1, but for use on other computers (under VPN) can be 0.0.0.0 *WARNING*")
    parser.add_argument("-p", "--web_port", default=5000,
                        help="port used with web address, eg -p 5000 would be 127.0.0.1:5000")


    args = parser.parse_args()

    app.run(host=args.web_address, port=args.web_port , debug=True)
