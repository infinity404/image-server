from flask import Flask, request, send_from_directory, redirect, url_for, render_template, Response
from werkzeug.utils import secure_filename
from functools import wraps
import tinys3
import random, os, config, uuid, time, models, database

#Setup
application = Flask(__name__)
s3_connection = tinys3.Connection(config.aws_access_key, config.aws_secret_key, tls=True, endpoint=config.aws_s3_endpoint)


#Load Configuration Variables
application.config['ALLOWED_EXTENSIONS'] = config.file_extentions
application.config['UPLOAD_DIRECTORY'] = config.upload_directory
application.config['URL_LENGTH'] = config.default_url_length
application.config['ALPHA_CHARS'] = config.url_characters
application.config['PATH'] = config.app_path
application.config['USER'] = config.app_username
application.config['PASSWORD'] = config.app_password

#Create the temporary directory to save images
if not os.path.exists(application.config['UPLOAD_DIRECTORY']):
    os.mkdir(application.config['UPLOAD_DIRECTORY'])


#App Methods
def strip_extenstion(filename: str) -> str:
    '''
    Strips the file extension from a file
    :param filename: A filename, such as 'example.txt'
    :return: A string of the extension type, such as 'txt', or None if there is no extension
    '''
    if '.' in filename:
        return filename.lower().rsplit('.', 1)[1]

    return None

def is_acceptable_filename(filename: str) -> bool:
    '''
    is_acceptable_file() helps check whether or not a file satisfied the requirement of being an image

    :param filename: Name of the file
    :return: A boolean indicating whether or not a file satisfies the requirements for an image
    '''

    return strip_extenstion(filename) in application.config['ALLOWED_EXTENSIONS']

def generate_random_string(length = application.config['URL_LENGTH']) -> str:
    '''
    :param length: Length of string to generate
    :return: A string of random digits from the ALPHA_CHARS config
    '''

    return ''.join([random.choice(application.config['ALPHA_CHARS']) for x in range(length)])

def check_auth(username, password):
    return username == application.config['USER'] and password == application.config['PASSWORD']

def authenticate():
    """Sends a 401 response that enables basic auth"""
    return Response(
    'Could not verify your access level for that URL.\n'
    'You have to login with proper credentials', 401,
    {'WWW-Authenticate': 'Basic realm="Login Required"'})


def make_s3_upload(bucket_name:str, destination_directory, key_name, path_to_file):
    with open(path_to_file, 'rb') as file:
        s3_path = os.path.join(destination_directory, key_name)
        s3_connection.upload(s3_path, file, bucket_name)

def add_to_database(guid, filename, file_url, timestamp, bucket=config.aws_s3_bucket_id, passphrase=None, accessability=1):
    database.Image.create(image_guid=guid, bucket=bucket, filename=filename, url=file_url, accessability=accessability, passphrase=passphrase, timestamp=timestamp)

def get_images():
    image_list = []

    for image in database.Image.select():
        im = models.Image(  file_url="http://{endpoint}/{bucket}/{filename}".format(
                                endpoint=config.aws_s3_endpoint, bucket=image.bucket,
                                filename=image.filename),
                            display_url="{app_path}\{shortcode}".format(
                                app_path=config.app_path,
                                shortcode=image.url),
                            timestamp=image.timestamp,
                            shortcode=image.url)
        image_list.append(im)

    return sorted(image_list, key= lambda image: -image.timestamp)


#Decorators
def requires_auth(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        auth = request.authorization
        if not auth or not check_auth(auth.username, auth.password):
            return authenticate()
        return f(*args, **kwargs)
    return decorated


#App Routes
@application.route('/')
def hello_world():
    return "ImageServer running on " + application.config['PATH']


@application.route('/<filename>/', methods=['GET'])
def get_image(filename = None):

    if filename is None:
        return 'Invalid URL specified.'

    if '.' in filename:
        filename = filename.split('.')[0]

    image_object = database.Image.select().where(database.Image.url == filename)

    if not image_object.exists():
        return '404'

    aws_url = 'http://' + config.aws_s3_endpoint + '/' + image_object[0].bucket + '/' + image_object[0].filename

    return render_template('display_image.html', model = models.TemplateDisplayImage(config.app_path, aws_url))



@application.route('/upload/', methods=['GET', 'POST'])
def upload_img():

    #Return default view if we aren't POSTing an image
    if not request.method == 'POST':
        return render_template('upload_form.html')

    #Get the file and check that it exists
    file = request.files['file']
    if not file:
        return "Error: no file was provided"

    #Check the file is valid
    if not is_acceptable_filename(file.filename):
        return "Error: An invalid file was provided. Files must be one of the following: " + ", ".join([('.' + x) for x in config.file_extentions])

    #Generate the file GUID
    file_id = uuid.uuid4()
    new_filename = '{0}.{1}'.format(file_id, strip_extenstion(file.filename))

    try:
        #Create a new shortcode
        new_url = generate_random_string()
        while database.Image.select().where(database.Image.url == new_url).exists():
            new_url = generate_random_string()

        #save the file to disk
        local_temp_path = os.path.join(application.config['UPLOAD_DIRECTORY'], new_filename)
        file.save(local_temp_path)

        #Upload to AWS
        make_s3_upload(config.aws_s3_bucket_id, config.aws_s3_bucket_path, new_filename, local_temp_path)
        add_to_database(file_id, os.path.join(config.aws_s3_bucket_path, new_filename), new_url, timestamp=int(time.time()))

        os.remove(local_temp_path)

        return '{}/{}'.format(config.app_path, new_url + '.png')

    except:
        return "An error occured while accessing the database. Please try again later."



@application.route('/delete', methods=['GET'])
@requires_auth
def delete_img():

    if not request.args.get('filename'):
        return "Invalid URL Parameters"

    sanitized_filename = secure_filename(request.args.get('filename'))
    query = database.Image.select().where(database.Image.url == sanitized_filename)

    if not query.exists():
        return "The image does not exist"

    image = query[0]
    s3_connection.delete(image.filename, bucket=config.aws_s3_bucket_id)
    image.delete_instance()

    redirect_url = request.args.get('redirect')

    if redirect_url:
        return redirect('/{}/'.format(redirect_url), code=302)
    else:
        return "The file has been deleted"


@application.route('/list/')
def list_files():

    template_model = models.TemplateListImage(config.app_path, get_images())
    return render_template('list_images.html', model = template_model)

if __name__ == '__main__':
    application.run('0.0.0.0', debug=True)
