"""
This file contatins an abstract class for CEDARS to interact with mongodb.
"""

from datetime import datetime
import logging
from werkzeug.security import check_password_hash, generate_password_hash
from dotenv import load_dotenv
from bson import ObjectId
from .database import mongo

load_dotenv()


def create_project(project_name, investigator_name, cedars_version):
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
        logging.info("Database already created.")
        return

    create_info_col(project_name, investigator_name, cedars_version)

    populate_annotations()
    populate_notes()
    populate_users()
    populate_query()

    logging.info("Database creation successful!")

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
    info = {"creation_time" : datetime.now(), "project" : project_name,
            "investigator" : investigator_name, "CEDARS_version" : cedars_version}

    collection.insert_one(info)
    logging.info("Created INFO collection.")

def get_info():
    """
    This function returns the info collection in the mongodb database.
    """
    return mongo.db.INFO.find_one_or_404()


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

    logging.info("Created ANNOTATIONS collection.")

    # This statement is used to create a collection.
    patients = mongo.db["PATIENTS"]
    logging.info("Created %s collection.", patients.name)

def populate_notes():
    """
    This function creates the notes collection in the mongodb database.
    The notes collection is used to store the patient's medical records.
    """
    notes = mongo.db["NOTES"]

    notes.create_index("patient_id", unique = False)
    notes.create_index("doc_id", unique = False)
    notes.create_index("text_id", unique = True)

    logging.info("Created NOTES collection.")


def populate_patients():
    """
    This function creates the notes collection in the mongodb database.
    The notes collection is used to store the patient's medical records.
    """
    notes = mongo.db["Patients"]

    notes.create_index("patient_id", unique = True)

    logging.info("Created Patients collection.")


def populate_users():
    """
    This function creates the users collection in the mongodb database.
    The users collection is used to store the credentials of users of the CEDARS system.
    """
    users = mongo.db["USERS"]

    users.create_index("user", unique = True)
    logging.info("Created USERS collection.")

def populate_query():
    """
    This function creates the query collection in the mongodb database.
    The query collection is used to store the regex queries that researchrs are using.
    """
    # Pylint disabled for pointless statement.
    # This statement is used to create a collection.
    query = mongo.db["QUERY"]
    logging.info("Created %s collection.", query.name)


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
    logging.info("Added user %s to database.", username)

# Pylint disabled due to too many arguments
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

    logging.info("Saved query : %s.", query)


def get_search_query():
    """
    This function is used to get the current search query from the database.
    All this data is kept in the QUERY collection.
    """
    query = mongo.db["QUERY"].find_one({"current" : True})
    return query["query"]


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

        if notes_collection.find_one({"text_id": note_info["text_id"]}):
            logging.error("Cancelling duplicate note entry")
        else:
            notes_collection.insert_one(note_info)
            patient_ids.add(note_info["patient_id"])

    patients_collection = mongo.db["PATIENTS"]
    for p_id in patient_ids:
        patient_info = {"patient_id": p_id,
                        "reviewed": False,
                        "locked": False,
                        "updated": False,
                        "admin_locked": False}

        if not patients_collection.find_one({"patient_id": p_id}):
            patients_collection.insert_one(patient_info)


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
    logging.debug("Retriving annotation #%s from database.", annotation_id)
    annotation = mongo.db["ANNOTATIONS"].find_one_or_404({ "_id" : ObjectId(annotation_id) })
    note = mongo.db["NOTES"].find_one({ "_id" : annotation["note_id"] })

    return note


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
        logging.debug("Retriving patient #%s from database.", patient['patient_id'])
        return patient["patient_id"]

    logging.debug("Failed to retrive any further un-reviewed patients from the database.")
    return None

def get_patient_annotation_ids(p_id):
    """
    Retrives all annotation IDs for annotations linked to a patient.

    Args:
        p_id (int) : Unique ID for a patient.
    Returns:
        annotations (list) : A list of all annotation IDs linked to that patient.
    """
    logging.debug("Retriving annotations for patient #%s from database.", str(p_id))
    annotation_ids = mongo.db["ANNOTATIONS"].find({"patient_id": p_id,
                                                   "reviewed" : False,
                                                   "isNegated" : False})

    return [str(id["_id"]) for id in annotation_ids]

def mark_annotation_reviewed(annotation_id):
    """
    Updates the annotation in the database to mark it as reviewed.

    Args:
        annotation_id (str) : Unique ID for the annotation.
    Returns:
        None
    """
    logging.debug("Marking annotation #%s as reviewed.", annotation_id)
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
    logging.debug("Updating date on annotation #%s to %s.", annotation_id, new_date)
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
    logging.debug("Deleting date on annotation #%s.", ObjectId(annotation_id))
    mongo.db["ANNOTATIONS"].update_one({"_id" : ObjectId(annotation_id)},
                                      { "$set": { "event_date" : None } })



def mark_patient_reviewed(patient_id, is_reviewed = True):
    """
    Updates the patient's status to reviewed in the database.

    Args:
        patient_id (int) : Unique ID for a patient.
        is_reviewed (bool) : True if patient's annotations have been reviewed.
    Returns:
        None
    """
    logging.debug("Marking patient #%s as reviewed.", patient_id)
    mongo.db["PATIENTS"].update_one({"patient_id" : patient_id},
                                                    { "$set": { "reviewed": is_reviewed } })

def add_annotation_comment(annotation_id, comment):
    """
    Stores a new comment for an annotation.

    Args:
        annotation_id (str) : Unique ID for the annotation.
        comment (str) : Text of the comment on this annotation.
    Returns:
        None
    """
    logging.debug("Adding comment to annotation #%s.", annotation_id)
    annotation = mongo.db["ANNOTATIONS"].find_one({ "_id" : ObjectId(annotation_id) })
    comments = annotation["comments"]
    comments.append(comment)
    mongo.db["ANNOTATIONS"].update_one({"_id" : ObjectId(annotation_id)},
                                       { "$set": { "comments" : comments } })

def empty_annotations():
    """
    Deletes all annotations from the database.
    """

    logging.info("Deleting all data in annotations collection.")
    annotations = mongo.db["ANNOTATIONS"]
    annotations.delete_many({})


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

def update_project_name(new_name):
    """
    Updates the project name in the INFO collection of the database.

    Args:
        new_name (str) : New name of the project.
    Returns:
        None
    """
    logging.debug("Updating project name to #%s.", new_name)
    mongo.db["INFO"].update_one({}, { "$set": { "project": new_name } })


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

    lemma_dist = {}
    for anno in mongo.db["ANNOTATIONS"].find({"isNegated" : False}):
        if anno['lemma'] in lemma_dist:
            lemma_dist[anno['lemma']] += 1
        else:
            lemma_dist[anno['lemma']] = 1

    stats['lemma_dist'] = lemma_dist

    return stats

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


def get_patient_notes(patient_id):
    """
    Returns all notes for that patient.

    Args:
        patient_id (int) : ID for the patient
    Returns:
        notes (list) : A list of all notes for that patient
    """
    mongodb_search_query = { "patient_id": patient_id }
    notes = list(mongo.db["NOTES"].find(mongodb_search_query))
    return notes

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


def remove_all_locked():
    """
    Sets the locked status of all patients to False.
    This is done when the server is shutting down.
    """
    patients_collection = mongo.db["PATIENTS"]
    patients_collection.update_many({},
                                    { "$set": { "locked": False } })

def is_admin_user(username):
    """check if the user is admin"""
    user = mongo.db["USERS"].find_one({'user' : username})

    if user is not None and user["is_admin"]:
        return True

    return False


def drop_database(name):
    """Clean Database"""
    mongo.cx.drop_database(name)
