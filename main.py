import flask
from flask import render_template
from flask import request
from flask import url_for
import uuid
from agenda import Agenda
from agenda import Appt

import json
import logging

# Date handling 
import arrow # Replacement for datetime, based on moment.js
import datetime # But we still need time
from dateutil import tz  # For interpreting local times


# OAuth2  - Google library implementation for convenience
from oauth2client import client
import httplib2   # used in oauth2 flow

# Google API for services 
from apiclient import discovery

###
# Globals
###
import CONFIG
app = flask.Flask(__name__)

SCOPES = 'https://www.googleapis.com/auth/calendar.readonly'
CLIENT_SECRET_FILE = CONFIG.GOOGLE_LICENSE_KEY  ## You'll need this
APPLICATION_NAME = 'MeetMe class project'

#############################
#
#  Pages (routed from URLs)
#
#############################

@app.route("/")
@app.route("/index")
def index():
  app.logger.debug("Entering index")
  if 'begin_date' not in flask.session:
    init_session_values()
  return render_template('index.html')

@app.route("/choose")
def choose():
    ## We'll need authorization to list calendars 
    ## I wanted to put what follows into a function, but had
    ## to pull it back here because the redirect has to be a
    ## 'return' 
    app.logger.debug("Checking credentials for Google calendar access")
    credentials = valid_credentials()
    if not credentials:
      app.logger.debug("Redirecting to authorization")
      return flask.redirect(flask.url_for('oauth2callback'))

    gcal_service = get_gcal_service(credentials)
    app.logger.debug("Returned from get_gcal_service")
    flask.session['calendars'] = list_calendars(gcal_service)
    return render_template('index.html')

####
#
#  Google calendar authorization:
#      Returns us to the main /choose screen after inserting
#      the calendar_service object in the session state.  May
#      redirect to OAuth server first, and may take multiple
#      trips through the oauth2 callback function.
#
#  Protocol for use ON EACH REQUEST: 
#     First, check for valid credentials
#     If we don't have valid credentials
#         Get credentials (jump to the oauth2 protocol)
#         (redirects back to /choose, this time with credentials)
#     If we do have valid credentials
#         Get the service object
#
#  The final result of successful authorization is a 'service'
#  object.  We use a 'service' object to actually retrieve data
#  from the Google services. Service objects are NOT serializable ---
#  we can't stash one in a cookie.  Instead, on each request we
#  get a fresh serivce object from our credentials, which are
#  serializable. 
#
#  Note that after authorization we always redirect to /choose;
#  If this is unsatisfactory, we'll need a session variable to use
#  as a 'continuation' or 'return address' to use instead. 
#
####

def valid_credentials():
    """
    Returns OAuth2 credentials if we have valid
    credentials in the session.  This is a 'truthy' value.
    Return None if we don't have credentials, or if they
    have expired or are otherwise invalid.  This is a 'falsy' value. 
    """
    if 'credentials' not in flask.session:
      return None

    credentials = client.OAuth2Credentials.from_json(
        flask.session['credentials'])

    if (credentials.invalid or
        credentials.access_token_expired):
      return None
    return credentials


def get_gcal_service(credentials):
  """
  We need a Google calendar 'service' object to obtain
  list of calendars, busy times, etc.  This requires
  authorization. If authorization is already in effect,
  we'll just return with the authorization. Otherwise,
  control flow will be interrupted by authorization, and we'll
  end up redirected back to /choose *without a service object*.
  Then the second call will succeed without additional authorization.
  """
  app.logger.debug("Entering get_gcal_service")
  http_auth = credentials.authorize(httplib2.Http())
  service = discovery.build('calendar', 'v3', http=http_auth)
  app.logger.debug("Returning service")
  return service

@app.route('/oauth2callback')
def oauth2callback():
  """
  The 'flow' has this one place to call back to.  We'll enter here
  more than once as steps in the flow are completed, and need to keep
  track of how far we've gotten. The first time we'll do the first
  step, the second time we'll skip the first step and do the second,
  and so on.
  """
  app.logger.debug("Entering oauth2callback")
  flow =  client.flow_from_clientsecrets(
      CLIENT_SECRET_FILE,
      scope= SCOPES,
      redirect_uri=flask.url_for('oauth2callback', _external=True))
  ## Note we are *not* redirecting above.  We are noting *where*
  ## we will redirect to, which is this function. 
  
  ## The *second* time we enter here, it's a callback 
  ## with 'code' set in the URL parameter.  If we don't
  ## see that, it must be the first time through, so we
  ## need to do step 1. 
  app.logger.debug("Got flow")
  if 'code' not in flask.request.args:
    app.logger.debug("Code not in flask.request.args")
    auth_uri = flow.step1_get_authorize_url()
    return flask.redirect(auth_uri)
    ## This will redirect back here, but the second time through
    ## we'll have the 'code' parameter set
  else:
    ## It's the second time through ... we can tell because
    ## we got the 'code' argument in the URL.
    app.logger.debug("Code was in flask.request.args")
    auth_code = flask.request.args.get('code')
    credentials = flow.step2_exchange(auth_code)
    flask.session['credentials'] = credentials.to_json()
    ## Now I can build the service and execute the query,
    ## but for the moment I'll just log it and go back to
    ## the main screen
    app.logger.debug("Got credentials")
    return flask.redirect(flask.url_for('choose'))

#####
#
#  Option setting:  Buttons or forms that add some
#     information into session state.  Don't do the
#     computation here; use of the information might
#     depend on what other information we have.
#   Setting an option sends us back to the main display
#      page, where we may put the new information to use. 
#
#####

@app.route('/setrange', methods=['POST'])
def setrange():
    """
    User chose a date range with the bootstrap daterange
    widget.
    """
    app.logger.debug("Entering setrange")  
    flask.flash("Setrange gave us '{}'".format(
      request.form.get('daterange')))
    daterange = request.form.get('daterange')
    start_time = request.form.get('start')
    end_time = request.form.get('end')
    app.logger.debug(start_time)
    app.logger.debug(end_time)

    flask.session['daterange'] = daterange
    flask.session['pick_start'] = start_time
    flask.session['pick_final'] = end_time
    daterange_parts = daterange.split()
    flask.session['begin_date'] = interpret_date(daterange_parts[0])
    flask.session['end_date'] = interpret_date(daterange_parts[2])
    flask.session['start_time'] = interpret_time(start_time)
    flask.session['end_time'] = interpret_time(end_time)
    app.logger.debug("Setrange parsed {} - {}  dates as {} - {}".format(
      daterange_parts[0], daterange_parts[1], 
      flask.session['begin_date'], flask.session['end_date']))
    return flask.redirect(flask.url_for("choose"))

####
#
#   Initialize session variables 
#
####

def init_session_values():
    """
    Start with some reasonable defaults for date and time ranges.
    Note this must be run in app context ... can't call from main. 
    """
    # Default date span = tomorrow to 1 week from now
    now = arrow.now('local')
    tomorrow = now.replace(days=+1)
    nextweek = now.replace(days=+7)
    flask.session["begin_date"] = tomorrow.floor('day').isoformat()
    flask.session["end_date"] = nextweek.ceil('day').isoformat()
    flask.session["daterange"] = "{} - {}".format(
        tomorrow.format("MM/DD/YYYY"),
        nextweek.format("MM/DD/YYYY"))
    # Default time span each day, 8 to 5
    flask.session["begin_time"] = interpret_time("9am")
    flask.session["end_time"] = interpret_time("5pm")

def interpret_time( text ):
    """
    Read time in a human-compatible format and
    interpret as ISO format with local timezone.
    May throw exception if time can't be interpreted. In that
    case it will also flash a message explaining accepted formats.
    """
    app.logger.debug("Decoding time '{}'".format(text))
    time_formats = ["ha", "h:mma",  "h:mm a", "H:mm"]
    try: 
        as_arrow = arrow.get(text, time_formats).replace(tzinfo=tz.tzlocal())
        app.logger.debug("Succeeded interpreting time")
    except:
        app.logger.debug("Failed to interpret time")
        flask.flash("Time '{}' didn't match accepted formats 13:30 or 1:30pm"
              .format(text))
        raise
    return as_arrow.isoformat()

def interpret_date( text ):
    """
    Convert text of date to ISO format used internally,
    with the local time zone.
    """
    try:
      as_arrow = arrow.get(text, "MM/DD/YYYY").replace(
          tzinfo=tz.tzlocal())
    except:
        flask.flash("Date '{}' didn't fit expected format 12/31/2001")
        raise
    return as_arrow.isoformat()

def next_day(isotext):
    """
    ISO date + 1 day (used in query to Google calendar)
    """
    as_arrow = arrow.get(isotext)
    return as_arrow.replace(days=+1).isoformat()

####
#
#  Functions (NOT pages) that return some information
#
####
  
def list_calendars(service):
    """
    Given a google 'service' object, return a list of
    calendars.  Each calendar is represented by a dict, so that
    it can be stored in the session object and converted to
    json for cookies. The returned list is sorted to have
    the primary calendar first, and selected (that is, displayed in
    Google Calendars web app) calendars before unselected calendars.
    """
    app.logger.debug("Entering list_calendars")  
    calendar_list = service.calendarList().list().execute()["items"]
    result = [ ]
    for cal in calendar_list:
        kind = cal["kind"]
        id = cal["id"]
        if "description" in cal: 
            desc = cal["description"]
        else:
            desc = "(no description)"
        summary = cal["summary"]
        # Optional binary attributes with False as default
        selected = ("selected" in cal) and cal["selected"]
        primary = ("primary" in cal) and cal["primary"]
        

        result.append(
          { "kind": kind,
            "id": id,
            "summary": summary,
            "selected": selected,
            "primary": primary
            })
    return sorted(result, key=cal_sort_key)

#####
#
# Find conflicts with selected time and date and the cals selected
#
#####
@app.route('/conflicts')
def find_conflicts():
    app.logger.debug("Entering find_conflicts")
    credentials = client.OAuth2Credentials.from_json(flask.session['credentials'])
    service = get_gcal_service(credentials)
    cals = request.args.get('cals', type=str)
    app.logger.debug(cals)
    split_cals = cals.split()
    final_events = []
    #go through each cal that was selected and check busy times
    for cal in split_cals:
        events = service.events().list(calendarId=cal,  pageToken=None).execute()
        print(events)
        #for each event within the current calendar
        for event in events['items']:
            #if the event is set to transparent skip it
            if 'transparancy' in event:
                continue
            start_time_date = arrow.get(event['start']['dateTime'])
            end_time_date = arrow.get(event['end']['dateTime'])

            if is_conflict(start_time_date, end_time_date):
                start = start_time_date.to('local').format('MM/DD/YYYY h:mm A')
                end = end_time_date.to('local').format('MM/DD/YYYY h:mm A')
                app.logger.debug(start)
                final_events.append({
                    "start": start,
                    "end": end,
                    "desc": event['summary']
                })

    free = find_free(final_events)
    fold_times(free, final_events)
    return "none"


def fold_times(free, events):
    """
    Takes the list of free times and list of events folds them together
    :param free: dict of free times
    :param events: dict of busy events
    :return: none
    """
    times = []
    for free_time in free:
        times.append(free_time)
    for busy_time in events:
        #have to do this so the lambda sort works properly
        busy_time['start'] = arrow.get(busy_time['start'], 'MM/DD/YYYY h:mm A').isoformat()
        busy_time['end'] = arrow.get(busy_time['end'], 'MM/DD/YYYY h:mm A').isoformat()
        times.append(busy_time)

    times.sort(key=lambda t: t['start'])
    final_times = []

    #now convert back to human readable
    for event in times:
        cur_event = {
            "desc": event['desc'],
            "start": arrow.get(event['start']).format('MM/DD/YYYY h:mm A'),
            "end": arrow.get(event['end']).format('MM/DD/YYYY h:mm A')
        }
        final_times.append(cur_event)

    flask.session['final_list'] = final_times
    app.logger.debug(final_times)
    return "none"

#######
#
# Finds free times given a list of busy times.
#
######
def find_free(events):
    """
    uses the not free events to find the free blocks
    :param events: dict of busy events
    :return: dict of free times
    """
    #pass in the event list
    agenda = Agenda.from_dict(events)
    app.logger.debug("Find Free Events")

    #Just get all the block in questions info
    start_date = arrow.get(flask.session['begin_date']).date()
    end_date = arrow.get(flask.session['end_date']).date()
    start_time = arrow.get(flask.session['start_time']).time()
    end_time = arrow.get(flask.session['end_time']).time()

    #Make two arrow objects for freeblock query
    begin = arrow.Arrow(start_date.year, start_date.month, start_date.day, start_time.hour, start_time.minute)
    end = arrow.Arrow(end_date.year, end_date.month, end_date.day, end_time.hour, end_time.minute)

    free = Appt(begin, end, "Free")
    free_time = agenda.complement(free)
    app.logger.debug(free_time.list_convert())

    #convert to dict for later use
    return free_time.list_convert()


def is_conflict(start, end):
        devent_start_end = [ start.date(), end.date()]
        tevent_start_end = [ start.time(), end.time()]
        dselected_start_end = [ arrow.get(flask.session['begin_date']).date(), arrow.get(flask.session['end_date']).date()]
        tselected_start_end = [ arrow.get(flask.session['start_time']).time(), arrow.get(flask.session['end_time']).time()]
        app.logger.debug(dselected_start_end)
        app.logger.debug(tselected_start_end)
        app.logger.debug(devent_start_end)
        app.logger.debug(tevent_start_end)

        #if the date falls between start and dates picked and during event return true
        if not (dselected_start_end[0] <= devent_start_end[0] <= dselected_start_end[1]) or not (dselected_start_end[0] <= devent_start_end[1] <= dselected_start_end[1]):
            app.logger.debug("'{}' <= '{}' <= '{}'".format(dselected_start_end[0], devent_start_end[0], dselected_start_end[1]))
            return False
        elif tevent_start_end[1] <= tselected_start_end[0]:
            app.logger.debug("Hello from time")
            return False
        elif tevent_start_end[0] >= tselected_start_end[1]:
            return False
        else:
            return True

@app.route('/busy')
def print_busy():
    #really does nothing but re-renders index.html once the session['final_list'] is filled
    return render_template('index.html')

def cal_sort_key( cal ):
    """
    Sort key for the list of calendars:  primary calendar first,
    then other selected calendars, then unselected calendars.
    (" " sorts before "X", and tuples are compared piecewise)
    """
    if cal["selected"]:
       selected_key = " "
    else:
       selected_key = "X"
    if cal["primary"]:
       primary_key = " "
    else:
       primary_key = "X"
    return (primary_key, selected_key, cal["summary"])


#################
#
# Functions used within the templates
#
#################

@app.template_filter( 'fmtdate' )
def format_arrow_date( date ):
    try: 
        normal = arrow.get( date )
        return normal.format("ddd MM/DD/YYYY")
    except:
        return "(bad date)"

@app.template_filter( 'fmttime' )
def format_arrow_time( time ):
    try:
        normal = arrow.get( time )
        return normal.format("HH:mm")
    except:
        return "(bad time)"
    
#############


if __name__ == "__main__":
  # App is created above so that it will
  # exist whether this is 'main' or not
  # (e.g., if we are running in a CGI script)

  app.secret_key = str(uuid.uuid4())  
  app.debug=CONFIG.DEBUG
  app.logger.setLevel(logging.DEBUG)
  # We run on localhost only if debugging,
  # otherwise accessible to world
  if CONFIG.DEBUG:
    # Reachable only from the same computer
    app.run(port=CONFIG.PORT)
  else:
    # Reachable from anywhere 
    app.run(port=CONFIG.PORT,host="0.0.0.0")
    
