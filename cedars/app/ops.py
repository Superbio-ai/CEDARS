"""
This page contatins the functions and the flask blueprint for the /proj_details route.
"""
import os
import re
from datetime import datetime, date
import tempfile
import pandas as pd
import pyarrow.parquet as pq
import flask
from dotenv import dotenv_values
from flask import (
    Blueprint, render_template,
    redirect, session, request,
    url_for, flash, g, jsonify
)

from loguru import logger
import requests
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename
from rq import Retry, Callback
from rq.registry import FailedJobRegistry
from rq.registry import FinishedJobRegistry, StartedJobRegistry
from . import db
from . import nlpprocessor
from . import auth
from .database import minio
from .api import load_pines_url, kill_pines_api
from .api import get_token_status
from .adjudication_handler import AdjudicationHandler
from .cedars_enums import PatientStatus


bp = Blueprint("ops", __name__, url_prefix="/ops")
config = dotenv_values(".env")

logger.enable(__name__)


def allowed_data_file(filename):
    """
    This function is used to check if a file has a valid extension for us to load tabular data from.

    Args:
        filepath (str) : The path to the file.
    Returns:
        (bool) : True if the file is of a supported type.
    """
    allowed_extensions = {'csv', 'xlsx', 'json', 'parquet', 'pickle', 'pkl', 'xml', 'csv.gz'}

    for extension in allowed_extensions:
        if filename.endswith('.' + extension):
            return True

    return False


def allowed_image_file(filename):
    """
    This function checks if a file is of a valid image filetype.

    Args:
        filename (str) : Name of the file trying to be loaded.
    Returns:
        (bool) : True if this is a supported image file type.
    """
    allowed_extensions = {'png', 'jpg', 'jpeg'}

    extension = filename.split(".")[-1]

    return extension in allowed_extensions


@bp.route("/project_details", methods=["GET", "POST"])
@auth.admin_required
def project_details():
    """
    This is a flask function for the backend logic for the proj_details route.
    It is used by the admin to view and alter details of the current project.
    """

    if request.method == "POST":
        if "update_project_name" in request.form:
            project_name = request.form.get("project_name").strip()
            old_name = db.get_proj_name()
            if old_name is None:
                project_id = os.getenv("PROJECT_ID", None)
                db.create_project(project_name, current_user.username,
                                    project_id = project_id)
                flash(f"Project **{project_name}** created.")
            else:
                project_info = db.get_info()
                project_id = project_info["project_id"]
                if len(project_name) > 0:
                    db.update_project_name(project_name)
                    flash(f"Project name updated to {project_name}.")
            return redirect("/")

        if "terminate" in request.form:
            terminate_clause = request.form.get("terminate_conf")
            if len(terminate_clause.strip()) > 0:
                if terminate_clause == 'DELETE EVERYTHING':
                    db.terminate_project()
                    # reset all rq queues
                    flask.current_app.task_queue.empty()
                    auth.logout_user()
                    session.clear()
                    flash("Project Terminated.")
                    return redirect("/")
            else:
                flash("Termination failed.. Please enter 'DELETE EVERYTHING' in confirmation")

    return render_template("ops/project_details.html",
                            **db.get_info())

@bp.route("/internal_processes", methods=["GET"])
@auth.admin_required
def internal_processes():
    """
    This is a flask function for the backend logic for the internal_processes route.
    It is used by a technical admin to perform special technical operations the current project.
    """

    # Get the RQ_DASHBOARD_URL environment variable,
    # if it does not exist use /rq as a default.
    rq_dashboard_url = os.getenv("RQ_DASHBOARD_URL", "/rq")

    return render_template("ops/internal_processes.html",
                            rq_dashboard_url = rq_dashboard_url,
                            **db.get_info())

def read_gz_csv(filename, *args, **kwargs):
    '''
    Function to read a GZIP compressed csv to a pandas DataFrame.
    '''
    return pd.read_csv(filename, compression='gzip', *args, **kwargs)


def load_pandas_dataframe(filepath, chunk_size=1000):
    """
    Load tabular data from a file into a pandas DataFrame.

    Args:
        filepath (str): The path to the file to load data from.
            Supported file extensions: csv, xlsx, json, parquet, pickle, pkl, xml.

    Returns:
        pd.DataFrame: DataFrame with the data from the file.

    Raises:
        ValueError: If the file extension is not supported.
        FileNotFoundError: If the file does not exist.
    """
    if not filepath:
        raise ValueError("Filepath must be provided.")

    extension = str(filepath).rsplit('.', maxsplit=1)[-1].lower()
    # If the extension is gz, we can assume it is a csv.gz file as this
    # is the only filecheck supported in the allowed_data_file check
    loaders = {
        'csv': pd.read_csv,
        'xlsx': pd.read_excel,
        'json': pd.read_json,
        'parquet': pd.read_parquet,
        'pickle': pd.read_pickle,
        'pkl': pd.read_pickle,
        'xml': pd.read_xml,
        'gz' : read_gz_csv,
    }

    if extension not in loaders:
        raise ValueError(f"""
                         Unsupported file extension '{extension}'.
                         Supported extensions are
                         {', '.join(loaders.keys())}.""")

    try:
        logger.info(filepath)
        obj = minio.get_object(g.bucket_name, filepath)
        local_directory = tempfile.gettempdir()
        os.makedirs(local_directory, exist_ok=True)
        local_filename = os.path.join(local_directory, os.path.basename(filepath))
        minio.fget_object(g.bucket_name, filepath, local_filename)
        logger.info(f"File downloaded successfully to {local_filename}")

        # Re-initialise object from minio to load it again
        if extension == 'parquet':
            parquet_file = pq.ParquetFile(local_filename)
            for batch in parquet_file.iter_batches(batch_size=chunk_size):
                yield batch.to_pandas()
        else:
            chunks = loaders[extension](local_filename, chunksize=chunk_size)
            for chunk in chunks:
                yield chunk

    except FileNotFoundError as exc:
        raise FileNotFoundError(f"File '{filepath}' not found.") from exc
    except Exception as exc:
        raise RuntimeError(f"Failed to load the file '{filepath}' due to: {str(exc)}") from exc
    finally:
        obj.close()
        obj.release_conn()
        if 'local_filepath' in locals() and os.path.exists(local_filename):
            os.remove(local_filename)
            logger.info(f"Removed temporary file: {local_filename}")


def prepare_note(note_info):
    date_format = '%Y-%m-%d'
    note_info["text_date"] = datetime.strptime(note_info["text_date"], date_format)
    note_info["reviewed"] = False
    note_info["text_id"] = str(note_info["text_id"]).strip()
    note_info["patient_id"] = str(note_info["patient_id"]).strip()
    return note_info


def prepare_patients(patient_ids):
    return [str(p_id).strip() for p_id in patient_ids]


def EMR_to_mongodb(filepath, chunk_size=1000):
    """
    This function is used to open a file and load its contents into the MongoDB database in chunks.

    Args:
        filepath (str): The path to the file to load data from.
        chunk_size (int): Number of rows to process per chunk.

    Returns:
        None
    """
    logger.info("Starting document migration to MongoDB database.")

    total_rows = 0
    total_chunks = 0
    all_patient_ids = []

    try:
        for chunk in load_pandas_dataframe(filepath, chunk_size):
            total_chunks += 1
            rows_in_chunk = len(chunk)
            total_rows += rows_in_chunk

            logger.info(f"Processing chunk {total_chunks} with {rows_in_chunk} rows")

            # Prepare notes
            notes_to_insert = [prepare_note(row.to_dict()) for _, row in chunk.iterrows()]

            # Collect patient IDs
            chunk_patient_ids = list(chunk['patient_id'].unique())
            chunk_patient_ids = prepare_patients(chunk_patient_ids)
            all_patient_ids.extend(chunk_patient_ids)

            # Bulk insert notes
            inserted_count = db.bulk_insert_notes(notes_to_insert)
            logger.info(f"Inserted {inserted_count} notes from chunk {total_chunks}")

        # store NOTES_SUMMARY such as first_note_date, last_note_date, total_notes etc.
        # to use a cache
        notes_summary_count = db.update_notes_summary()
        logger.info(f"Updated {notes_summary_count} notes summary")
        # Bulk upsert patients
        upserted_count_patients, _ = db.bulk_upsert_patients(all_patient_ids)
        logger.info(f"Upserted {upserted_count_patients} patients")
        logger.info(f"Completed document migration to MongoDB database. "
                    f"Total rows processed: {total_rows}, "
                    f"Total chunks processed: {total_chunks}, "
                    f"Total unique patients: {len(all_patient_ids)}")

    except Exception as e:
        logger.error(f"An error occurred during document migration: {str(e)}")
        flash(f"Failed to upload data: {str(e)}")
        raise

@bp.route("/upload_data", methods=["GET", "POST"])
@auth.admin_required
def upload_data():
    """
    This is a flask function for the backend logic to upload a file to the database.
    """
    filename = None
    if request.method == "POST":
        # if db.get_task(f"upload_and_process:{current_user.username}"):
        #     flash("A file is already being processed.")
        #     return redirect(request.url)
        minio_file = request.form.get("miniofile")
        if minio_file != "None" and minio_file is not None:
            logger.info(f"Using minio file: {minio_file}")
            filename = minio_file
        else:
            if 'data_file' not in request.files:
                flash('No file part')
                return redirect(request.url)
            file = request.files['data_file']
            if file.filename == '':
                flash('No selected file')
                return redirect(request.url)
            if file and not allowed_data_file(file.filename):
                flash("""Invalid file type.
                      Please upload a .csv, .xlsx, .json, .parquet, .pickle, .pkl, or .xml file.""")
                return redirect(request.url)

            filename = f"uploaded_files/{secure_filename(file.filename)}"
            size = os.fstat(file.fileno()).st_size

            try:
                minio.put_object(g.bucket_name,
                                 filename,
                                 file,
                                 size,
                                 part_size=10*1024*1024,
                                 num_parallel_uploads=10
                                 )
                logger.info(f"File - {file.filename} uploaded successfully.")
                flash(f"{filename} uploaded successfully.")
            except Exception as e:
                filename = None
                flash(f"Failed to upload file: {str(e)}")
                return redirect(request.url)

        if filename:
            try:
                EMR_to_mongodb(filename)
                flash(f"Data from {filename} uploaded to the database.")
                return redirect(url_for('ops.upload_query'))
            except Exception as e:
                flash(f"Failed to upload data: {str(e)}")
                return redirect(request.url)
    try:
        files = [(obj.object_name, obj.size)
                 for obj in minio.list_objects(g.bucket_name,
                                               prefix="uploaded_files/")]
    except Exception as e:
        flash(f"Error listing files: {e}")
        files = []

    return render_template("ops/upload_file.html", files=files, **db.get_info())


@bp.route("/upload_query", methods=["GET", "POST"])
@auth.admin_required
def upload_query():
    """
    This is a flask function for the backend logic
    to upload a csv file to the database.
    """
    # TODO: make regex translation using chatGPT if API is available

    if request.method == "GET":
        current_query = db.get_search_query()
        return render_template("ops/upload_query.html",
                               current_query=current_query,
                               **db.get_info(),
                               **db.get_search_query_details())

    search_query = request.form.get("regex_query")
    logger.info(f"Received search query: {search_query}")
    search_query_pattern = r'^\s*(\(\s*[a-zsA-Z0-9*?]+(\s+AND\s+[a-zA-Z0-9*?]+)*\s*\)|[a-zA-Z0-9*?]+)(\s*OR\s+(\(\s*[a-zA-Z0-9*?]+(\s+AND\s+[a-zA-Z0-9*?]+)*\s*\)|[a-zA-Z0-9*?]+))*\s*$'
    try:
        re.match(search_query_pattern, search_query)
    except re.error as e:
        flash(f"Invalid query - {e}")
        logger.debug(f"Invalid query: {e}")
        return render_template("ops/upload_query.html", **db.get_info())

    use_pines = bool(request.form.get("nlp_apply"))
    superbio_api_token = session.get('superbio_api_token')
    if superbio_api_token is not None and use_pines:
        # If using a PINES server via superbio,
        # ensure that the current token works properly
        token_status = get_token_status(superbio_api_token)
        if token_status['has_expired'] is True:
            # If we are using a token, and this token has expired
            # then we cancell the process and do not add anything to the queue.
            logger.info('The current token has expired. Logging our user.')
            redirect(url_for('auth.logout'))
        elif token_status['is_valid'] is False:
            logger.error(f'Passed invalid token : {superbio_api_token}')
            flash("Invalid superbio token.")
            return redirect(url_for("ops.upload_query"))

    if use_pines:
        is_pines_available = init_pines_connection(superbio_api_token)
        if is_pines_available is False:
            # PINES could not load successfully
            flash("Could not load PINES server.")
            return redirect(url_for("ops.upload_query"))

    use_negation = False  # bool(request.form.get("view_negations"))
    hide_duplicates = not bool(request.form.get("keep_duplicates"))
    skip_after_event = bool(request.form.get("skip_after_event"))

    tag_query = {
        "exact": False,
        "nlp_apply": use_pines
    }
    new_query_added = db.save_query(search_query, use_negation,
                                    hide_duplicates, skip_after_event, tag_query)

    # TODO: add a javascript confirm box to make sure the user wants to update the query
    if new_query_added:
        db.empty_annotations()
        db.reset_patient_reviewed()

    if "patient_id" in session:
        session.pop("patient_id")
        session.modified = True

    do_nlp_processing()
    return redirect(url_for("stats_page.stats_route"))

@bp.route("/start_process")
def do_nlp_processing():
    """
    Run NLP workers
    TODO: requeue failed jobs
    """
    nlp_processor = nlpprocessor.NlpProcessor()
    pt_ids = db.get_patient_ids()
    superbio_api_token = session.get('superbio_api_token')

    # add task to the queue
    for patient in pt_ids:
        flask.current_app.task_queue.enqueue(
            nlp_processor.automatic_nlp_processor,
            args=(patient,),
            job_id=f'spacy:{patient}',
            description=f"Processing patient {patient} with spacy",
            retry=Retry(max=3),
            on_success=Callback(callback_job_success),
            on_failure=Callback(callback_job_failure),
            kwargs={
                "user": current_user.username,
                "job_id": f'spacy:{patient}',
                "superbio_api_token" : superbio_api_token,
                "description": f"Processing patient {patient} with spacy"
            }
        )
    return redirect(url_for("ops.get_job_status"))


def callback_job_success(job, connection, result, *args, **kwargs):
    '''
    A callback function to handle the event where
    a job from the task queue is completed successfully.
    '''
    db.report_success(job)

    if len(list(db.get_tasks_in_progress())) == 0:
        # Send a spin down request to the PINES Server if we are using superbio
        # This will occur when all tasks are completed
        if job.kwargs['superbio_api_token'] is not None:
            close_pines_connection(job.kwargs['superbio_api_token'])

def callback_job_failure(job, connection, result, *args, **kwargs):
    '''
    A callback function to handle the event where
    a job from the task queue has failed.
    '''
    db.report_failure(job)

    if len(list(db.get_tasks_in_progress())) == 0:
        # Send a spin down request to the PINES Server if we are using superbio
        # This will occur when all tasks are completed
        if job.kwargs['superbio_api_token'] is not None:
            close_pines_connection(job.kwargs['superbio_api_token'])

def init_pines_connection(superbio_api_token = None):
    '''
    Initializes the PINES url in the INFO col.
    If no server is available this is marked as None.

    Args :
        - superbio_api_token (str) : Access token for superbio server if one is being used.
    
    Returns :
        (bool) : True if a valid pines url has been found.
                            False if not valid pines url available.
    '''
    project_info = db.get_info()
    project_id = project_info["project_id"]

    try:
        pines_url, is_url_from_api = load_pines_url(project_id,
                                        superbio_api_token=superbio_api_token)
    except requests.exceptions.HTTPError as e:
        logger.error(f"Got HTTP error when trying to start PINES server : {e}")
        pines_url, is_url_from_api = None, False
    except Exception as e:
        logger.error(f"Got error when trying to access PINES server : {e}")
        pines_url, is_url_from_api = None, False

    db.create_pines_info(pines_url, is_url_from_api)
    if pines_url is not None:
        return True

    return False

def close_pines_connection(superbio_api_token):
    '''
    Closes the PINES server if using a superbio API.
    '''
    project_info = db.get_info()
    project_id = project_info["project_id"]
    token_status = get_token_status(superbio_api_token)

    if token_status['is_valid'] and token_status['has_expired'] is False:
        kill_pines_api(project_id, superbio_api_token)
    else:
        # If has_token_expired returns None (invalid token).
        logger.error("Cannot shut down remote PINES server with an API call as this is not a valid token.")

@bp.route("/job_status", methods=["GET"])
def get_job_status():
    """
    Directs the user to the job status page.
    """
    return render_template("ops/job_status.html",
                           tasks=db.get_tasks_in_progress(), **db.get_info())


@bp.route('/queue_stats', methods=['GET'])
def queue_stats():
    """
    Returns details of how many jobs are left in the queue and the status of
    completed jobs. Is used for the project statistics page.
    """
    queue_length = len(flask.current_app.task_queue)
    failed_job_registry = FailedJobRegistry(queue=flask.current_app.task_queue)
    failed_jobs = len(failed_job_registry)
    finished_job_registry = FinishedJobRegistry(queue=flask.current_app.task_queue)
    successful_jobs = len(finished_job_registry)
    return flask.jsonify({'queue_length': queue_length,
                          'failed_jobs': failed_jobs,
                          'successful_jobs': successful_jobs
                          })

@bp.route("/save_adjudications", methods=["GET", "POST"])
@login_required
def save_adjudications():
    """
    Handle logic for the save_adjudications route.
    Used to edit and review annotations.
    """
    if session.get("patient_id") is None:
        return redirect(url_for("ops.adjudicate_records"))

    adjudication_handler = AdjudicationHandler(session['patient_id'])
    adjudication_handler.load_from_patient_data(session['patient_id'],
                                                session['patient_data'])

    current_annotation_id = adjudication_handler.get_curr_annotation_id()
    db.add_comment(current_annotation_id, request.form['comment'].strip())
    patient_id = session['patient_id']
    skip_after_event = db.get_search_query(query_key="skip_after_event")

    action = request.form['submit_button']
    is_shift_performed = False
    if action == 'new_date':
        # Make sure to remove all skipped markings before a new date is entered
        db.revert_skipped_annotations(patient_id)
        adjudication_handler.reset_all_skipped()

        new_date = request.form['date_entry']
        date_format = '%Y-%m-%d'
        new_date = datetime.strptime(new_date, date_format)
        annotations_after_event = []
        if skip_after_event:
            annotations_after_event = db.get_annotations_post_event(patient_id,
                                                                    new_date)
            db.mark_annotations_post_event(patient_id, new_date)

        db.mark_annotation_reviewed(current_annotation_id, current_user.username)
        db.update_event_date(patient_id, new_date, current_annotation_id)

        adjudication_handler.mark_event_date(new_date, current_annotation_id,
                                             annotations_after_event)

    elif action == 'del_date':
        db.delete_event_date(patient_id)
        db.revert_skipped_annotations(patient_id)
        db.revert_annotation_reviewed(current_annotation_id, current_user.username)
        adjudication_handler.delete_event_date()
    elif action == 'adjudicate':
        db.mark_annotation_reviewed(current_annotation_id, current_user.username)
        adjudication_handler._adjudicate_annotation()
    else:
        # Must be a shift action
        adjudication_handler.perform_shift(action)
        is_shift_performed = True

    session["patient_data"] = adjudication_handler.get_patient_data()

    # We do not skip to the next patient if the current operation was just a shift.
    # This is done as users may want to view notes for a patient that has already been
    # reviewed.
    if adjudication_handler.is_patient_reviewed() and not is_shift_performed:
        db.set_patient_lock_status(patient_id, False)
        db.mark_patient_reviewed(patient_id,
                                         reviewed_by=current_user.username)
        session.pop("patient_id")
        session.pop("patient_data")

    session.modified = True

    db.upsert_patient_records(patient_id, datetime.now(),
                              updated_by = current_user.username)

    # the session has been cleared so get the next patient
    if session.get("patient_id") is None:
        return redirect(url_for("ops.adjudicate_records"))

    return redirect(url_for("ops.show_annotation"))


@bp.route("/show_annotation", methods=["GET"])
def show_annotation():
    """
    Formats and displays the current annotation being viewed by the user.
    """
    index = session.get("index", 0)
    adjudication_handler = AdjudicationHandler(session['patient_id'])
    adjudication_handler.load_from_patient_data(session['patient_id'],
                                                session['patient_data'])
    annotation_id = adjudication_handler.get_curr_annotation_id()

    annotation = db.get_annotation(annotation_id)
    note = db.get_annotation_note(annotation_id)
    if not note:
        flash("Annotation note not found.")
        return redirect(url_for("ops.adjudicate_records"))

    comments = db.get_patient_by_id(session['patient_id'])["comments"]
    annotations_for_note = db.get_all_annotations_for_note(note["text_id"])
    annotations_for_sentence = db.get_all_annotations_for_sentence(note["text_id"],
                                                                   annotation["sentence_number"])

    annotation_data = adjudication_handler.get_annotation_details(annotation,
                                                                  note, comments,
                                                                  annotations_for_note,
                                                                  annotations_for_sentence)

    return render_template("ops/adjudicate_records.html",
                           name = current_user.username,
                           **annotation_data,
                           **db.get_info())


@bp.route("/adjudicate_records", methods=["GET", "POST"])
@login_required
def adjudicate_records():
    """
    Adjudication Workflow:

    ### Get the next available patient

    1. The first time this page is hit, get a patient who is not reviewed from the database
    2. Since the patient is not reviewed in the db,
                            they should have annotations, if not, skip to the next patientd

    ### Search for a patient

    1. If the user searches for a patient, get the patient from the database
    2. If the patient has no annotations, skip to the next patient

    ### show the annotation
    3. Get the first annotation and show it to the user
    4. User can adjudicate the annotation or browse back and forth
    5. If the annotation has an event date, mark the note as reviewed
        5.a. If the note has been reviewed, mark the note as reviewed
        5.b. If the all notes have been reviewed, mark the patient as reviewed
    6. If the annotation has no event date, show the next annotation
    7. If there are no more annotations to be reviewed, mark the patient as reviewed
    8. All annotations are a mongodb document.

    """

    patient_id = None
    if request.method == "GET":
        if session.get("patient_id") is not None:
            if db.get_patient_lock_status(session.get("patient_id")) is False:
                logger.info(f"Getting patient: {session.get('patient_id')} from session")
                return redirect(url_for("ops.show_annotation"))

            logger.info(f"Patient {session.get('patient_id')} is locked.")
            logger.info("Retrieving next patient.")

        patient_id = db.get_patients_to_annotate()
    else:
        if session.get("patient_id") is not None:
            db.set_patient_lock_status(session.get("patient_id"), False)
            session.pop("patient_id", None)

        search_patient = str(request.form.get("patient_id")).strip()
        is_patient_locked = False
        if search_patient and len(search_patient) > 0:
            patient = db.get_patient_by_id(search_patient)
            if patient is None:
                patient_id = None
            else:
                is_patient_locked = db.get_patient_lock_status(search_patient)
                if is_patient_locked is False:
                    patient_id = patient["patient_id"]
                else:
                    patient_id = None

        if patient_id is None:
            # if the search return no patient, get the next patient
            if is_patient_locked:
                flash(f"Patient {search_patient} is currently being reviewed by another user. Showing next patient")
            else:
                flash(f"Patient {search_patient} does not exist. Showing next patient")
            patient_id = db.get_patients_to_annotate()

    if patient_id is None:
        return render_template("ops/annotations_complete.html", **db.get_info())

    raw_annotations = db.get_all_annotations_for_patient(patient_id)
    hide_duplicates = db.get_search_query("hide_duplicates")
    stored_event_date = db.get_event_date(patient_id)
    stored_annotation_id = db.get_event_annotation_id(patient_id)

    adjudication_handler = AdjudicationHandler(patient_id)
    patient_data, annotations_with_duplicates = adjudication_handler.init_patient_data(raw_annotations,
                                           hide_duplicates, stored_event_date,
                                           stored_annotation_id)

    for annotation_id in annotations_with_duplicates:
        db.mark_annotation_reviewed(annotation_id, current_user.username)

    if len(patient_data["annotation_ids"]) > 0:
        # Only lock the patient for annotation if
        # there are annotations that exist
        db.set_patient_lock_status(patient_id, True)

    patient_status = adjudication_handler.get_patient_status()

    if patient_status == PatientStatus.NO_ANNOTATIONS:
        logger.info(f"Patient {patient_id} has no annotations. Showing next patient")
        flash(f"Patient {patient_id} has no annotations. Showing next patient")
        db.set_patient_lock_status(patient_id, False)
        return redirect(url_for("ops.adjudicate_records"))

    elif patient_status == PatientStatus.REVIEWED_WITH_EVENT:
        flash(f"Patient {patient_id} has been reviewed. Showing annotation where event date was marked. ")
        logger.info(f"Showing annotations for patient {patient_id}.")
        session["patient_id"] = patient_id
        session['patient_data'] = patient_data

    elif patient_status == PatientStatus.REVIEWED_NO_EVENT:
        flash(f"Patient {patient_id} has no annotations left to review. Showing all annotations.")
        session["patient_id"] = patient_id
        session['patient_data'] = patient_data

    else:
        logger.info(f"Showing annotations for patient {patient_id}.")
        session["patient_id"] = patient_id
        session['patient_data'] = patient_data

    session.modified = True
    return redirect(url_for("ops.show_annotation"))

@bp.route("/unlock_patient", methods=["POST"])
def unlock_current_patient():
    """
    Sets the locked status of the patient in the session to False.
    """
    patient_id = session["patient_id"]
    if patient_id is not None:
        db.set_patient_lock_status(patient_id, False)
        session["patient_id"] = None
        return jsonify({"message": f"Unlocking patient # {patient_id}."}), 200

    return jsonify({"error": "No patient to unlock."}), 200

def get_next_annotation_index(unreviewed_annotations, current_index):
    '''
    Given a list of the review status of annotations and the current index
    find the next unreviewed annotation to review.

    Args :
        - unreviewed_annotations (list[int]) : For each index, 0 indicates that an annotation is reviewed
                                                               1 indicates that an annotation has been reviewed
        - current_index (int) : Index of the annotation that was just reviewed,
    
    Returns :
        - next_index (int) : Index of the next unreviewed annotation after the current one,
                                will return the same index as the current index if no unreviewed annotations left.
    
    '''

    i = current_index + 1
    while i != current_index:
        if i == len(unreviewed_annotations):
            i = 0

        if unreviewed_annotations[i] == 1:
            break

        i += 1

    return i

def get_download_filename(is_full_download=False):
    '''
    Returns the filename for a new download task.

    Args :
        - is_full_download (bool) : True if all of the results 
                                    (including the key sentences)
                                    are to be downloaded.

    Returns :
        - filename (string) : A string in the format
                              {project_name}_{timestamp}_{downloadtype}.csv
    '''
    project_name = db.get_proj_name()
    timestamp = datetime.now()
    timestamp = timestamp.strftime("%Y-%m-%d_%H_%M_%S")

    if is_full_download:
        return f"annotations_full_{project_name}_{timestamp}.csv"

    return f"annotations_compact_{project_name}_{timestamp}.csv"

@bp.route('/download_page')
@bp.route('/download_page/<job_id>')
@auth.admin_required
def download_page(job_id=None):
    """
    Loads the page where an admin can download the results
    of annotations made for that project.
    """
    files = [(obj.object_name.rsplit("/", 1)[-1],
              obj.size,
              obj.last_modified.strftime("%Y-%m-%d %H:%M:%S")
              ) for obj in minio.list_objects(
                   g.bucket_name,
                   prefix="annotated_files/")]

    if job_id is not None:
        return flask.jsonify({"files": files}), 202

    return render_template('ops/download.html', job_id=job_id, files=files, **db.get_info())


@bp.route('/download_annotations', methods=["POST"])
@auth.admin_required
def download_file(filename='annotations.csv'):
    """
    ##### Download Completed Annotations

    This generates a CSV file with the following specifications:
    1. Find all patients in the PATIENTS database,
            these patients become a single row in the CSV file.
    2. For each patient -
        a. list the number of total notes in the database
        b. list the number of reviewed notes
        c. list the number of total sentences from annotations
        d. list the number of reviewed sentences
        e. list all sentences as a list of strings
        f. add event date from the annotations for each patient
        g. add the first and last note date for each patient
    3. Convert all columns to proper datatypes
    """
    logger.info("Downloading annotations")
    filename = request.form.get("filename")
    file = minio.get_object(g.bucket_name, f"annotated_files/{filename}")
    logger.info(f"Downloaded annotations from s3: {filename}")

    return flask.Response(
        file.stream(32*1024),
        mimetype='text/csv',
        headers={"Content-Disposition": f"attachment;filename=cedars_{filename}"}
    )


@bp.route('/create_download_task', methods=["GET"])
@auth.admin_required
def create_download():
    """
    Create a download task for annotations
    """

    download_filename = get_download_filename()
    job = flask.current_app.ops_queue.enqueue(
        db.download_annotations, download_filename,
    )
    return flask.jsonify({'job_id': job.get_id()}), 202


@bp.route('/create_download_task_full', methods=["GET"])
@auth.admin_required
def create_download_full():
    """
    Create a download task for annotations
    """

    download_filename = get_download_filename(True)
    job = flask.current_app.ops_queue.enqueue(
        db.download_annotations, download_filename, True
    )

    return flask.jsonify({'job_id': job.get_id()}), 202

@bp.route('/delete_download_file', methods=["POST"])
@auth.admin_required
def delete_download_file():
    """
    Deletes a download file from the current minio bucket.
    """

    filename = request.form.get("filename")
    minio.remove_object(g.bucket_name, f"annotated_files/{filename}")
    logger.info(f"Successfully removed {filename} from minio server.")

    return redirect("/ops/download_page")

@bp.route('/update_results_collection', methods=["GET"])
@auth.admin_required
def update_results_collection():
    """
    Creates and updates the RESULTS collection.
    """

    job = flask.current_app.ops_queue.enqueue(db.update_patient_results,
                                                True)

    return flask.jsonify({'job_id': job.get_id()}), 202

@bp.route('/check_job/<job_id>')
@auth.admin_required
def check_job(job_id):
    """
    Returns the status of a job to the frontend.
    """
    logger.info(f"Checking job {job_id}")
    job = flask.current_app.ops_queue.fetch_job(job_id)
    if job.is_finished:
        return flask.jsonify({'status': 'finished', 'result': job.result}), 200
    elif job.is_failed:
        return flask.jsonify({'status': 'failed', 'error': str(job.exc_info)}), 500
    else:
        return flask.jsonify({'status': 'in_progress'}), 202
