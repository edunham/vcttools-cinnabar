# Servo lookup table for sorting PRs
STATUS_TO_PRIORITY = {
    'success': 0,
    'pending': 1,
    'approved': 2,
    '': 3,
    'error': 4,
    'failure': 5,
}

# Messages for Buildbot comments. Remove if Buildbot goes away.
INTERRUPTED_BY_AUTOLAND_FMT = 'Interrupted by Autoland ({})'
INTERRUPTED_BY_AUTOLAND_RE = re.compile(r'Interrupted by Autoland \((.+?)\)')

# Delay before giving up on retrying a PR
TEST_TIMEOUT = 3600 * 10

# Cute debugging messages came in with Homu
PORTAL_TURRET_DIALOG = ["Target acquired", "Activated", "Who's there?", "There you are"]
PORTAL_TURRET_IMAGE = "https://cloud.githubusercontent.com/assets/1617736/22222924/c07b2a1c-e16d-11e6-91b3-ac659550585c.png"

# HG max attempts to transplant before bailing
MAX_TRANSPLANT_ATTEMPTS = 50

# max updates to post to reviewboard / iteration
MOZREVIEW_COMMENT_LIMIT = 10

# time to wait before attempting to update MozReview after a failure to post
MOZREVIEW_RETRY_DELAY = datetime.timedelta(minutes=5)

# time to wait before retrying a transplant
TRANSPLANT_RETRY_DELAY = datetime.timedelta(minutes=5)

