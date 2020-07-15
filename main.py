from flask import Flask, render_template, request, redirect, url_for, json
from src.job import Job
import src.queue as q
import os
import base64

app = Flask(__name__)

#Initialise an empty job queue
jobQueue = q.JobsQueue(maxsize = 10)

#initialise variables
gather_cmd = ""
demul_cmd = ""
minion_cmd = "test"
override_data = False

'''
def run_command():
    command = "sleep 8; echo '*** it worked ****'"
    os.system(command)
'''

@app.route("/home")
def home():
    #Update displayed queue on home page
    queueList = []
    for item in list(jobQueue.queue):
        queueList.append(item.job_name)
    
    queueDict = {'jobs':queueList}
    displayQueue = json.htmlsafe_dumps(queueDict)
    return render_template("home.html", queue = displayQueue)

@app.route("/about")
def about():
	return render_template("about.html")

@app.route("/parameters", methods = ["POST","GET"])
def parameters():
    if request.method == "POST":
        #test
        #os.system("artic gather --min-length 400 --max-length 800 --prefix ebov-mayinga --directory /Users/iggygetout/Documents/binf6111_project/data/20190830_1509_MN22126_AAQ411_9efc5448 --no-fast5s")
        #get parameters
        job_name = request.form.get('job_name')
        input_folder = request.form.get('input_folder')
        read_file = request.form.get('read_file')
        primer_scheme = request.form.get('primer_scheme')
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
        #variables to add to job class
        num_samples = request.form.get('num_samples')

        #if user agrees output can override files with the same name in output folder
        if request.form.get('override_data'):
            override_data = True
        else:
            override_data = False

        errors = {}
        if not os.path.isdir(input_folder):
            errors['input_folder'] = "Invalid path."
        elif len(os.listdir(input_folder)) == 0:
            errors['input_folder'] = "Directory is empty."

        #if read file is specified by user
        if read_file:
            if not os.path.isfile(read_file):
                errors['read_file'] = "Invalid path/file."
        else:
            #to be filled later
            read_file = ""

        #if no output folder entered, creates one inside of input folder
        if not output_folder:
            output_folder = input_folder + "/output"

        #if the output folder does not exist, it is created
        #maybe need to put in checks for this?
        if not os.path.exists(output_folder):
            make_dir = 'mkdir "' + output_folder + '"'
            os.system(make_dir)

        #check length parameters are valid

        if min_length.isdigit() == False:
            errors['invalid_length'] = "Invalid minimum length."
            if max_length.isdigit() == False:
                errors['invalid_length'] = "Invalid maximum and minimum length."
        elif max_length.isdigit() == False:
            errors['invalid_length'] = "Invalid maximum length."
        elif int(max_length) < int(min_length):
            errors['invalid_length'] = "Invalid parameters: Maximum length smaller than minimum length."
            
        if jobQueue.full():
            errors['full_queue'] = "Job queue is full."
        
        print("Errors: ", errors)

        if len(errors) != 0:
            return render_template('parameters.html', errors=errors, name=job_name, input_folder=input_folder,read_file=read_file,primer_scheme=primer_scheme,output_folder=output_folder)

        #no spaces in the job name - messes up commands
        job_name = job_name.replace(" ", "_")
        
        #Create a new instance of the Job class
        new_job = Job(job_name, input_folder, read_file, primer_scheme, output_folder, normalise, num_threads, pipeline, min_length, max_length, bwa, skip_nanopolish, dry_run, override_data, num_samples)
        
        #Add job to queue
        jobQueue.put(new_job)

        #Generate commands (using methods of job)
        '''gather_cmd = new_job.generateGatherCmd()
        demul_cmd = ""
        minion_cmd = new_job.generateMinionCmd()

        #need to encode - '/' in file path screws with url
        gather_cmd = base64.b64encode(gather_cmd.encode())
        output_folder = base64.b64encode(output_folder.encode())
        minion_cmd = base64.b64encode(minion_cmd.encode())'''

        #return render_template("progress.html", min_cmd = minion_cmd)
        #return redirect(url_for('progress', gather_cmd = gather_cmd, min_cmd = minion_cmd, job_name = job_name, output_folder = output_folder))
        return redirect(url_for('progress', job_name=job_name))
        
    return render_template("parameters.html")

@app.route("/progress/<job_name>", methods = ["GET", "POST"])
def progress(job_name):
    job = jobQueue.getJobByName(job_name)
    return render_template("progress.html", output = job.executeCmds())

#not sure if this should be a get method
@app.route("/output", methods = ["GET", "POST"])
def output(): #need to update to take in output folder and job name as parameters
    #job_name = request.args.get('job_name')
    #output_folder = request.args.get('output_folder')
    job_name = "My Job1"
    output_folder = '.'
    output_files = []

    if job_name:
        if output_folder:
            for (dirpath, dirnames, filenames) in os.walk(output_folder):
                print(filenames)
                output_files.extend(filenames)

    if request.method == "POST":
        plots = []
        if request.form.get('barplot') == "yes":
            plots.append("barplot")
        if request.form.get('boxplot') == "yes":
            plots.append("boxplot")
        if not plots:
            plots = "Nothing selected."
        if request.form['submit_button'] == 'Preview':
            return render_template("output.html", job_name=job_name, output_folder=output_folder, output_files=output_files, preview_plots=plots)
        if request.form['submit_button'] == 'Download':
            return render_template("output.html", job_name=job_name, output_folder=output_folder, output_files=output_files, download_plots=plots)
    return render_template("output.html", job_name=job_name, output_folder=output_folder, output_files=output_files)


if __name__ == "__main__":
    app.run(debug=True)
