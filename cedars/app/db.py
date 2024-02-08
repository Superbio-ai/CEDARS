"""
This file contatins an abstract class for CEDARS to interact with mongodb.
"""

import os
from datetime import datetime
import requests
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv
from faker import Faker
from bson import ObjectId
from loguru import logger
from typing import Optional
import re
from .database import mongo

load_dotenv()
logger.enable(__name__)
fake = Faker()

# Create collections and indexes
def create_project(project_name, investigator_name, cedars_version = "0.1.0"):
    """
    This function creates all the collections in the mongodb database for CEDARS.

    Args:
        project_name (str) : Name of the research project
        investigator_name (str) : Name of the investigator on this project
        cedars_version (str) : Version of CEDARS used for this project
    Returns:
        None
    """
    if mongo.db["INFO"].find_one() is not None:
        logger.info("Database already created.")
        return

    create_info_col(project_name, investigator_name, cedars_version)

    populate_annotations()
    populate_notes()
    populate_users()
    populate_query()

    logger.info("Database creation successful!")

def create_info_col(project_name, investigator_name, cedars_version):
    """
    This function creates the info collection in the mongodb database.
    The info collection is used to store meta-data regarding the current project.

    Args:
        project_name (str) : Name of the research project
        investigator_name (str) : Name of the investigator on this project
        cedars_version (str) : Version of CEDARS used for this project
    Returns:
        None
    """
    collection = mongo.db["INFO"]
    info = {"creation_time" : datetime.now(),
            "project" : project_name,
            "investigator" : investigator_name,
            "CEDARS_version" : cedars_version}

    collection.insert_one(info)
    logger.info("Created INFO collection.")

def populate_annotations():
    """
    This function creates the annotations and patients collections in the mongodb database.
    The annotations collection is used to store the NLP annotations generated by our NLP model.
    The patients collection is used to store the patient ids as well as their current status.
    """
    annotations = mongo.db["ANNOTATIONS"]


    annotations.create_index("patient_id", unique = False)
    annotations.create_index("note_id", unique = False)
    annotations.create_index("text_id", unique = False)
    annotations.create_index("sentence_number", unique = False)
    annotations.create_index("start_index", unique = False)

    logger.info("Created ANNOTATIONS collection.")

    # This statement is used to create a collection.
    patients = mongo.db["PATIENTS"]
    logger.info(f"Created {patients.name} collection.")

def populate_notes():
    """
    This function creates the notes collection in the mongodb database.
    The notes collection is used to store the patient's medical records.
    """
    notes = mongo.db["NOTES"]

    notes.create_index("patient_id", unique = False)
    notes.create_index("doc_id", unique = False)
    notes.create_index("text_id", unique = True)

    logger.info("Created NOTES collection.")

def populate_patients():
    """
    This function creates the notes collection in the mongodb database.
    The notes collection is used to store the patient's medical records.
    """
    notes = mongo.db["Patients"]

    notes.create_index("patient_id", unique = True)

    logger.info("Created Patients collection.")

def populate_users():
    """
    This function creates the users collection in the mongodb database.
    The users collection is used to store the credentials of users of the CEDARS system.
    """
    users = mongo.db["USERS"]

    users.create_index("user", unique = True)
    logger.info("Created USERS collection.")

def populate_query():
    """
    This function creates the query collection in the mongodb database.
    The query collection is used to store the regex queries that researchrs are using.
    """
    # Pylint disabled for pointless statement.
    # This statement is used to create a collection.
    query = mongo.db["QUERY"]
    logger.info("Created %s collection.", query.name)

# Insert functions
def add_user(username, password, is_admin=False):
    """
    This function is used to add a new user to the database.
    All this data is kept in the USERS collection.

    Args:
        username (str) : The name of this user.
        password (str) : The password this user will need to login to the system.
    Returns:
        None
    """
    info = {
        "user" : username,
        "password" : password,
        "is_admin": is_admin,
        "date_created" : datetime.now()
    }
    mongo.db["USERS"].insert_one(info)
    logger.info(f"Added user {username} to database.")

def add_project_user(username, password, is_admin = False):
    """
    Adds a new user to the project database.

    Args:
        username (str)  : The name of the new user
        password (str)  : The user's password
        is_admin (bool) : True if the new user is the project admin
                          (used when initializing the project)
    Returns:
        None
    """
    password_hash = generate_password_hash(password)
    data = {"user" : username, "password" : password_hash, "admin" : is_admin}
    mongo.db["USERS"].insert_one(data.copy())

def save_query(query, exclude_negated, hide_duplicates, #pylint: disable=R0913
                skip_after_event, tag_query, date_min = None, date_max = None):

    """
    This function is used to save a regex query to the database.
    All this data is kept in the QUERY collection.

    Args:
        query (str) : The regex query.
        exclude_negated (bool) : True if we want to exclude negated tokens.
        hide_duplicates (bool) : True if we want to restrict duplicate queries.
        skip_after_event (bool) : True if sentences occurring
                                    after a recorded clinical event are to be skipped.
        tag_query (dict of mapping [str : list]) :
                                    Key words to include or exclude in the search.
        date_min (str) : Smallest date for valid query.
        date_max (str) : Greatest date for valid query.
    Returns:
        None
    """
    info = {
        "query" : query,
        "exclude_negated" : exclude_negated,
        "hide_duplicates" : hide_duplicates,
        "skip_after_event" : skip_after_event,
        "tag_query" : tag_query,
        "date_min" : date_min,
        "date_max" : date_max
    }

    collection = mongo.db["QUERY"]
    # only one query is current at a time.
    # TODO: make a query history and enable multiple queries.
    info["current"] = True

    collection.update_one({"current": True}, {"$set": {"current": False}})
    collection.insert_one(info)

    logger.info(f"Saved query : {query}.")

def upload_notes(documents):
    """
    This function is used to take a dataframe of patient records
    and save it to the mongodb database.

    Args:
        documents (pandas dataframe) : Dataframe with all the records of a paticular patient.
    Returns:
        None
    """
    notes_collection = mongo.db["NOTES"]
    patient_ids = set()
    for i in range(len(documents)):
        note_info = documents.iloc[i].to_dict()

        date_format = '%Y-%m-%d'
        datetime_obj = datetime.strptime(note_info["text_date"], date_format)
        note_info["text_date"] = datetime_obj
        note_info["reviewed"] = False

        if notes_collection.find_one({"text_id": note_info["text_id"]}):
            logger.error("Cancelling duplicate note entry")
        else:
            notes_collection.insert_one(note_info)
            patient_ids.add(note_info["patient_id"])
        if i+1 % 10 == 0:
            logger.info(f"Uploaded {i}/{len(documents)} notes")

    patients_collection = mongo.db["PATIENTS"]
    for p_id in patient_ids:
        patient_info = {"patient_id": p_id,
                        "reviewed": False,
                        "locked": False,
                        "updated": False,
                        "admin_locked": False}

        if not patients_collection.find_one({"patient_id": p_id}):
            patients_collection.insert_one(patient_info)

def insert_one_annotation(annotation):
    """
    Adds an annotation to the database.

    Args:
        annotation (dict) : The annotation we are inserting
    Returns:
        None
    """
    annotations_collection = mongo.db["ANNOTATIONS"]

    annotations_collection.insert_one(annotation)

# Get functions
def get_user(username):
    """
    This function is used to get a user from the database.

    Args:
        username (str) : The name of the user to get.
    Returns:
        user (dict) : The user object from the database.
    """
    user = mongo.db["USERS"].find_one({"user" : username})
    return user

def get_search_query():
    """
    This function is used to get the current search query from the database.
    All this data is kept in the QUERY collection.
    """
    query = mongo.db["QUERY"].find_one({"current" : True})
    return query["query"]

def get_info():
    """
    This function returns the info collection in the mongodb database.
    """
    return mongo.db.INFO.find_one_or_404()

def  get_all_annotations_for_note(note_id):
    """
    This function is used to get all the annotations for a particular note
    after removing negated annotations.
    Order of annotations -
        text_date (ascending)
        note_start_index (ascending)
    """
    annotations = mongo.db["ANNOTATIONS"].find({"note_id" : note_id,
                                                "isNegated": False}).sort([("text_date", 1),("setence_number", 1)])
    return list(annotations)

def get_annotation(annotation_id):
    """
    Retrives annotation from mongodb.

    Args:
        annotation_id (str) : Unique ID for the annotation.
    Returns:
        annotation (dict) : Dictionary for an annotation from mongodb.
            The keys are the attribute names.
            The values are the values of the attribute in that record.
    """
    annotation = mongo.db["ANNOTATIONS"].find_one({ "_id" : ObjectId(annotation_id) })

    return annotation

def get_annotation_note(annotation_id):
    """
    Retrives note linked to a paticular annotation.

    Args:
        annotation_id (str) : Unique ID for the annotation.
    Returns:
        note (dict) : Dictionary for a note from mongodb.
                      The keys are the attribute names.
                      The values are the values of the attribute in that record.
    """
    logger.debug(f"Retriving annotation #{annotation_id} from database.")
    annotation = mongo.db["ANNOTATIONS"].find_one_or_404({ "_id" : ObjectId(annotation_id) })
    note = mongo.db["NOTES"].find_one({"text_id" : annotation["note_id"] })

    return note


def get_patient_by_id(patient_id):
    """
    Retrives a single patient from mongodb.

    Args:
        patient_id (int) : Unique ID for a patient.
    Returns:
        patient (dict) : Dictionary for a patient from mongodb.
                         The keys are the attribute names.
                         The values are the values of the attribute in that record.
    """
    logger.debug(f"Retriving patient #{patient_id} from database.")
    patient = mongo.db["PATIENTS"].find_one({"patient_id" : patient_id})

    return patient

def get_patient():
    """
    Retrives a single patient ID who has not yet been reviewed and is not currently locked.
    The chosen patient is simply the first one in the database that has not yet been reviewed.

    Args:
        None
    Returns:
        patient_id (int) : Unique ID for a patient.
    """

    patient = mongo.db["PATIENTS"].find_one({"reviewed" : False,
                                             "locked" : False})

    if patient is not None and "patient_id" in patient.keys():
        logger.debug(f"Retriving patient #{patient['patient_id']} from database.", )
        return patient["patient_id"]

    logger.info("Failed to retrive any further un-reviewed patients from the database.")
    return None


def get_patients_to_annotate():
    """
    Retrieves a patient that have not been reviewed

    Args:
        None
    Returns:
        patient_to_annotate: A single patient that needs to manually reviewed 
    """
    logger.debug("Retriving all un-reviewed patients from database.")
    patients_to_annotate = mongo.db["PATIENTS"].find({"reviewed" : False})

    # check is this patient has any unreviewed annotations
    for patient in patients_to_annotate:
        patient_id = patient["patient_id"]
        annotations = get_patient_annotation_ids(patient_id)
        if len(annotations) > 0:
            return patient_id
        else:
            continue

    return None

def get_documents_to_annotate():
    """
    Retrives all documents that have not been annotated.
    """
    logger.debug("Retriving all annotated documents from database.")
    documents_to_annotate = mongo.db["NOTES"].aggregate(
        [{
            "$lookup": {
                "from": "ANNOTATIONS",
                "localField": "text_id",
                "foreignField": "text_id",
                "as": "annotations"
            }
        },
        {
            "$match": {
                "annotations": {"$eq": []}
            }
        }])

    return documents_to_annotate

def get_patient_annotation_ids(p_id, reviewed = False, key = "_id"):
    """
    Retrives all annotation IDs for annotations linked to a patient.

    Args:
        p_id (int) : Unique ID for a patient.
        reviewed (bool) : True if we want to get reviewed annotations.
    Returns:
        annotations (list) : A list of all annotation IDs linked to that patient.
    """
    logger.debug(f"Retriving annotations for patient #{p_id} from database.")
    annotation_ids = mongo.db["ANNOTATIONS"].find({"patient_id": p_id,
                                                   "reviewed" : reviewed,
                                                   "isNegated" : False}).sort([("note_id", 1),
                                                                               ('text_date', 1),
                                                                               ("sentence_number", 1)])

    return [str(id[key]) for id in annotation_ids]

def get_annotation_date(annotation_id):
    """
    Retrives the event date for an annotation.
    """
    logger.debug(f"Retriving date on annotation #{ObjectId(annotation_id)}.")
    annotation = mongo.db["ANNOTATIONS"].find_one({"_id" : ObjectId(annotation_id)})
    if "event_date" in annotation.keys():
        return annotation["event_date"]
    return None


def get_event_date(patient_id):
    """
    Find the event date from the annotations for a patient.
    """
    logger.debug(f"Retriving event date for patient #{patient_id}.")
    annotations = mongo.db["ANNOTATIONS"].find({"patient_id" : patient_id,
                                               "event_date" : {"$ne" : None}}).sort([("event_date", 1)])
    annotations = list(annotations)
    if len(annotations) > 0:
        return annotations[0]["event_date"]

    return None


def get_first_note_date_for_patient(patient_id):
    """
    Retrives the date of the first note for a patient.

    Args:
        patient_id (int) : Unique ID for a patient.
    Returns:
        note_date (datetime) : The date of the first note for the patient.
    """
    logger.debug(f"Retriving first note date for patient #{patient_id}.")
    note = mongo.db["NOTES"].find_one({"patient_id" : patient_id},
                                      sort = [("text_date", 1)])

    if not note:
        return None
    return note["text_date"]


def get_last_note_date_for_patient(patient_id):
    """
    Retrives the date of the last note for a patient.

    Args:
        patient_id (int) : Unique ID for a patient.
    Returns:
        note_date (datetime) : The date of the last note for the patient.
    """
    logger.debug(f"Retriving last note date for patient #{patient_id}.")
    note = mongo.db["NOTES"].find_one({"patient_id" : patient_id},
                                      sort = [("text_date", -1)])

    if not note:
        return None
    return note["text_date"]

def get_all_annotations():
    """
    Returns a list of all annotations from the database.

    Args:
        None
    Returns:
        Annotations (list) : This is a list of all annotations from the database.
    """
    annotations = mongo.db["ANNOTATIONS"].find()

    return list(annotations)

def get_proj_name():
    """
    Returns the name of the current project.

    Args:
        None
    Returns:
        proj_name (str) : The name of the current CEDARS project.
    """

    proj_info = mongo.db["INFO"].find_one_or_404()
    proj_name = proj_info["project"]
    return proj_name

def get_curr_version():
    """
    Returns the name of the current project.

    Args:
        None
    Returns:
        proj_name (str) : The name of the current CEDARS project.
    """

    proj_info = mongo.db["INFO"].find_one()

    return proj_info["CEDARS_version"]

def get_project_users():
    """
    Returns all the usernames for approved users (including the admin) for this project

    Args:
        None
    Returns:
        usernames (list) : List of all usernames for approved users
                           (including the admin) for this project
    """
    users = mongo.db["USERS"].find({})

    return [user["user"] for user in users]

def get_all_patients():
    """
    Returns all the patients in this project

    Args:
        None
    Returns:
        patients (list) : List of all patients in this project
    """
    patients = mongo.db["PATIENTS"].find()

    return list(patients)

def get_patient_ids():
    """
    Returns all the patient IDs in this project

    Args:
        None
    Returns:
        patient_ids (list) : List of all patient IDs in this project
    """
    patients = mongo.db["PATIENTS"].find()

    return [patient["patient_id"] for patient in patients]

def get_patient_lock_status(patient_id):
    """
    Updates the status of the patient to be locked or unlocked.

    Args:
        patient_id (int) : ID for the patient we are locking / unlocking
    Returns:
        status (bool) : True if the patient is locked, False otherwise.
            If no such patient is found, we return None.

    Raises:
        None
    """
    patient = mongo.db["PATIENTS"].find_one({"patient_id" : patient_id})
    return patient["locked"]


def get_all_notes(patient_id):
    """
    Returns all notes for that patient.
    """
    notes = mongo.db["NOTES"].find({"patient_id": patient_id})
    return list(notes)

def get_patient_notes(patient_id, reviewed = False):
    """
    Returns all notes for that patient.

    Args:
        patient_id (int) : ID for the patient
    Returns:
        notes (list) : A list of all notes for that patient
    """
    mongodb_search_query = { "patient_id": patient_id, "reviewed": reviewed }
    notes = list(mongo.db["NOTES"].find(mongodb_search_query))
    return notes

def get_total_counts(collection_name: str) -> int:
    """
    Returns the total number of documents in a collection.

    Args:
        collection_name (str) : The name of the collection to search.
    Returns:
        count (int) : The number of documents in the collection.
    """
    return mongo.db[collection_name].count_documents({})

def get_annotated_notes_for_patient(patient_id: int) -> list[str]:
    """
    For a given patient, list all note_ids which have matching keyword
    annotations

    Args:
        patient_id (int) : The patient_id for which we want to retrieve the
            annotated notes

    Returns:
        notes (list[str]) : List of note_ids for the patient which have
            matching keyword annotations
    """
    annotations = mongo.db["ANNOTATIONS"].find({"patient_id": patient_id})
    notes = set()
    for annotation in annotations:
        notes.add(annotation["note_id"])

    return list(notes)
    

# update functions
def update_project_name(new_name):
    """
    Updates the project name in the INFO collection of the database.

    Args:
        new_name (str) : New name of the project.
    Returns:
        None
    """
    logger.info(f"Updating project name to #{new_name}")
    mongo.db["INFO"].update_one({}, { "$set": { "project": new_name } })

def mark_annotation_reviewed(annotation_id):
    """
    Updates the annotation in the database to mark it as reviewed.

    Args:
        annotation_id (str) : Unique ID for the annotation.
    Returns:
        None
    """
    logger.debug(f"Marking annotation #{annotation_id} as reviewed.")
    mongo.db["ANNOTATIONS"].update_one({"_id" : ObjectId(annotation_id)},
                                       {"$set": { "reviewed": True } })

def update_annotation_date(annotation_id, new_date):
    """
    Enters a new event date for an annotation.

    Args:
        annotation_id (str) : Unique ID for the annotation.
        new_date (str) : The new value to update the event date of an annotation with.
            Must be in the format YYYY-MM-DD .
    Returns:
        None
    """
    # TODO: UTC dates
    logger.debug(f"Updating date on annotation #{annotation_id} to {new_date}.")
    date_format = '%Y-%m-%d'
    datetime_obj = datetime.strptime(new_date, date_format)
    mongo.db["ANNOTATIONS"].update_one({"_id" : ObjectId(annotation_id)},
                                       { "$set": { "event_date" : datetime_obj } })

def delete_annotation_date(annotation_id):
    """
    Deletes the event date for an annotation.

    Args:
        annotation_id (str) : Unique ID for the annotation.
    Returns:
        None
    """
    logger.debug(f"Deleting date on annotation #{ObjectId(annotation_id)}.")
    mongo.db["ANNOTATIONS"].update_one({"_id" : ObjectId(annotation_id)},
                                      { "$set": { "event_date" : None } })

def mark_patient_reviewed(patient_id, reviewed_by: str, is_reviewed = True):
    """
    Updates the patient's status to reviewed in the database.

    Args:
        patient_id (int) : Unique ID for a patient.
        reviewed_by (str) : The name of the user who reviewed the patient.
        is_reviewed (bool) : True if patient's annotations have been reviewed.
    Returns:
        None
    """
    logger.debug(f"Marking patient #{patient_id} as reviewed.")
    mongo.db["PATIENTS"].update_one({"patient_id" : patient_id},
                                                    { "$set": { "reviewed": is_reviewed,
                                                               "reviewed_by": reviewed_by } })


def mark_note_reviewed(note_id, reviewed_by: str):
    """
    Updates the note's status to reviewed in the database.
    """
    logger.debug(f"Marking note #{note_id} as reviewed.")
    mongo.db["NOTES"].update_one({"text_id" : note_id},
                                 { "$set": { "reviewed": True,
                                             "reviewed_by": reviewed_by} })
def reset_patient_reviewed():
    """
    Update all patients, notes to be un-reviewed.
    """
    mongo.db["PATIENTS"].update_many({},
                                     { "$set": { "reviewed": False } })
    mongo.db["NOTES"].update_many({}, { "$set": { "reviewed": False } })


def add_annotation_comment(annotation_id, comment):
    """
    Stores a new comment for an annotation.

    Args:
        annotation_id (str) : Unique ID for the annotation.
        comment (str) : Text of the comment on this annotation.
    Returns:
        None
    """
    if len(comment) == 0:
        logger.info("No comment entered.")
        return
    logger.debug(f"Adding comment to annotation #{annotation_id}")
    annotation = mongo.db["ANNOTATIONS"].find_one({ "_id" : ObjectId(annotation_id) })
    comments = annotation["comments"]
    comments.append(comment)
    mongo.db["ANNOTATIONS"].update_one({"_id" : ObjectId(annotation_id)},
                                       { "$set": { "comments" : comments } })

def set_patient_lock_status(patient_id, status):
    """
    Updates the status of the patient to be locked or unlocked.

    Args:
        patient_id (int) : ID for the patient we are locking / unlocking
        status (bool) : True if the patient is being locked, False otherwise.

    Returns:
        None
    """


    patients_collection = mongo.db["PATIENTS"]
    patients_collection.update_one({"patient_id" : patient_id},
                                   { "$set": { "locked": status } })


def remove_all_locked():
    """
    Sets the locked status of all patients to False.
    This is done when the server is shutting down.
    """
    patients_collection = mongo.db["PATIENTS"]
    patients_collection.update_many({},
                                    { "$set": { "locked": False } })


def update_annotation_reviewed(note_id: str) -> int:
    """
    Mark all annotations for a note as reviewed.

    Args:
        note_id (str) : The note_id for which we want to mark all annotations as reviewed.
    Returns:
        count (int) : The number of annotations that were marked as reviewed.
    """
    annotations_collection = mongo.db["ANNOTATIONS"]
    result = annotations_collection.update_many({"note_id": note_id},
                                               {"$set": {"reviewed": True}})
    return result.modified_count

# delete functions
def empty_annotations():
    """
    Deletes all annotations from the database
    """

    logger.info("Deleting all data in annotations collection.")
    annotations = mongo.db["ANNOTATIONS"]
    annotations.delete_many({})
    mongo.db["PINES"].delete_many({})


def drop_database(name):
    """Clean Database"""
    mongo.cx.drop_database(name)

# utility functions
def check_password(username, password):
    """
    Checks if the password matches the password of that user from the database.

    Args:
        username (str) : The name of the new user
        password (str) : The password entered by the user.

    Returns:
        (bool) : True if the password matches the password of that user from the database.
    """

    user = mongo.db["USERS"].find_one({"user" : username})

    return "password" in user and check_password_hash(user["password"], password)

def is_admin_user(username):
    """check if the user is admin"""
    user = mongo.db["USERS"].find_one({'user' : username})

    if user is not None and user["is_admin"]:
        return True

    return False

# stats functions
def get_curr_stats():
    """
    Returns basic statistics for the project

    Args:
        None
    Returns:
        stats (list) : List of basic statistics for the project. These include :
            1. number_of_patients (number of unique patients in the database)
            2. number_of_annotated_patients (number of patiens who had notes
                in which key words were found)
            3. number_of_reviewed
                        (number of patients who have been reviewed for the current query)
    """
    # TODO: use aggregation pipeline
    stats = {}
    patients = get_all_patients()

    stats["number_of_patients"] = len(list(patients))

    annotations = mongo.db["ANNOTATIONS"].find({"isNegated" : False})
    unique_patients = {annotation["patient_id"] for annotation in annotations}

    stats["number_of_annotated_patients"] = len(unique_patients)

    num_reviewed_annotations = 0

    for p_id in unique_patients:
        p_anno = mongo.db["PATIENTS"].find_one({"patient_id" : p_id})

        if p_anno["reviewed"] is True:
            num_reviewed_annotations += 1

    stats["number_of_reviewed"] = num_reviewed_annotations

    pipeline = [
            {"$match": {"isNegated": False}},
            {"$group": {"_id": "$token", "count": {"$sum": 1}}}
    ]

    # Perform aggregation
    results = mongo.db.ANNOTATIONS.aggregate(pipeline)

    # Convert aggregation results to a dictionary
    lemma_dist = {result["_id"]: result["count"] for result in results}
    lemma_dist = {re.sub(r'[^a-zA-Z0-9-_ ]', '', k): v for k, v in sorted(
        lemma_dist.items(), key=lambda item: item[1], reverse=True)}
    stats['lemma_dist'] = lemma_dist
    logger.debug(stats)
    return stats

# pines functions
def get_prediction(note: str) -> float:
    """
    Get prediction from endpoint. Text goes in the POST request.
    """
    url = f'{os.getenv("PINES_API_URL")}/predict'
    data = {'text': note}
    try:
        response = requests.post(url, json=data, timeout=20)
        response.raise_for_status()
        res = response.json()["prediction"]
        score = res.get("score")
        label = res.get("label")
        if isinstance(label, str):
            score = 1 - score if "0" in label else score
        else:
            score = 1 - score if label == 0 else score
        logger.debug(f"Got prediction for note: {note} with score: {score} and label: {label}")
        return score
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to get prediction for note: {note}")
        raise e
    

def get_note_prediction_from_db(note_id: str,
                                pines_collection_name: str = "PINES") -> Optional[float]:
    """
    Retrieve the prediction score for a given from the database

    Args:
        pines_collection_name (str): The name of the collection in the database
        note_id (str): The note_id for which we want to retrieve the prediction
    
    Returns:
        float: The prediction score for the note
    """
    pines_collection = mongo.db[pines_collection_name]
    query = {"text_id": note_id}

    pines_pred = pines_collection.find_one(query)
    if pines_pred:
        logger.debug(f"Found prediction in db for : {note_id}: {pines_pred.get('predicted_score')}")
        return round(pines_pred.get("predicted_score"), 2)
    logger.debug(f"Prediction not found in db for : {note_id}")
    return None


def predict_and_save(text_ids: Optional[list[str]] = None,
                     note_collection_name: str = "NOTES",
                     pines_collection_name: str = "PINES",
                     force_update: bool = False) -> None:
    """
    Predict and save the predictions for the given text_ids.
    """
    notes_collection = mongo.db[note_collection_name]
    pines_collection = mongo.db[pines_collection_name]
    query = {}
    if text_ids is not None:
        query = {"text_id": {"$in": text_ids}}
    
    cedars_notes = notes_collection.find(query)
    for note in cedars_notes:
        note_id = note.get("text_id")
        if force_update or get_note_prediction_from_db(note_id, pines_collection_name) is None:
            logger.info(f"Predicting for note: {note_id}")
            prediction = get_prediction(note.get("text"))
            pines_collection.insert_one({
                "text_id": note_id,
                "text": note.get("text"),
                "patient_id": note.get("patient_id"),
                "predicted_score": prediction,
                "report_type": note.get("text_tag_3"),
                "document_type": note.get("text_tag_1")
                })
            

def terminate_project():
    """
    Reset the database to the initial state.
    """
    logger.info("Terminating project.")
    mongo.db.drop_collection("ANNOTATIONS")
    mongo.db.drop_collection("NOTES")
    mongo.db.drop_collection("PATIENTS")
    mongo.db.drop_collection("USERS")
    mongo.db.drop_collection("QUERY")
    mongo.db.drop_collection("PINES")
    create_project(project_name=fake.slug(),
                   investigator_name=fake.name())
