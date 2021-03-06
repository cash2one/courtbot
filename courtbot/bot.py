from collections import defaultdict
import logging
import re
from time import sleep

from slackclient import SlackClient
from slackclient._server import SlackConnectionError

from courtbot import constants, settings
from courtbot.exceptions import AvailabilityError
from courtbot.spider import Spider
from courtbot.utils import conversational_join, slash_separated_date


logger = logging.getLogger(__name__)


class Bot:
    """
    Bot capable of reading from and writing to Slack.

    Uses Slack's RTM API. For more about the RTM API, see documentation at
    http://slackapi.github.io/python-slackclient/real_time_messaging.html.
    """
    def __init__(self):
        logger.info('Initializing courtbot.')

        self.client = SlackClient(settings.SLACK_TOKEN)
        self.spider = Spider()

        self.users = {}
        self.id = None
        self.cache_users()

        self.actions = {
            'health': [
                'alive',
                'health',
            ],
            'help': [
                'explain',
                'help',
            ],
            'show': [
                'available',
                'check',
                'look',
                'show',
            ],
            'book': [
                'book',
                'grab',
                'reserve',
            ],
        }

    def cache_users(self):
        """
        Cache team user info.

        Finds and caches user IDs and handles belonging to everyone on the Slack
        team, including the bot. The bot ID is used to identify messages which
        mention the bot. Other IDs are used to look up handles so the bot can
        mention other users in its messages.
        """
        users = self.client.api_call('users.list')['members']
        for user in users:
            id = user['id']
            handle = user['name']

            self.users[id] = handle

            if handle == settings.SLACK_HANDLE:
                self.id = id

                logger.info(f'Bot ID is [{self.id}].')

    def connect(self):
        """
        Connect to the Slack RTM API.
        """
        logger.info('Attempting to connect to Slack.')

        if self.client.rtm_connect():
            logger.info('Connected to Slack.')

            while True:
                events = self.read()

                try:
                    self.parse(events)
                except:
                    logger.exception('Event parsing failed.')

                sleep(settings.SLACK_RTM_READ_DELAY)
        else:
            logger.error('Failed to connect to Slack.')
            raise SlackConnectionError

    def read(self):
        """
        Read from the RTM websocket stream.

        See https://api.slack.com/rtm#events.

        Returns:
            defaultdict: Lists of events, keyed by type. Empty if no new events
                were found when reading the stream.
        """
        events = defaultdict(list)
        stream = self.client.rtm_read()

        for event in stream:
            type = event['type']
            events[type].append(event)

        return events

    def parse(self, events):
        """
        Parse events from the RTM stream.

        Arguments:
            events (defaultdict): Events to parse.
        """
        messages = events['message']

        for message in messages:
            logger.info(f'Parsing message [{message}].')

            text = message.get('text')
            if text and self.id in text:
                text = text.lower()
                timestamp = message['ts']

                logger.info(f'Message at [{timestamp}] contains bot ID [{self.id}].')

                for action, triggers in self.actions.items():
                    if any(trigger.lower() in text for trigger in triggers):
                        logger.info(f'Triggering the [{action}] action.')

                        getattr(self, action)(message)
                        break
                else:
                    logger.info(f'Message at [{timestamp}] does not include any triggers.')

    def health(self, message):
        """
        Post a health message.

        Arguments:
            message (dict): Message mentioning the bot which includes a 'health' trigger.
        """
        user = self.users[message['user']]
        self.post(message['channel'], f'@{user} I\'m here!')

    def help(self, message):
        """
        Post a message explaining how to use courtbot.

        Arguments:
            message (dict): Message mentioning the bot which includes a 'help' trigger.
        """
        user = self.users[message['user']]

        lines = []
        for action, triggers in self.actions.items():
            docstring = [line.strip() for line in getattr(self, action).__doc__.split('\n') if line]

            triggers = [f'`{trigger}`' for trigger in triggers]
            words = conversational_join(triggers, conjunction='or')

            lines.append(
                f'*{action}*: {docstring[0]} Trigger by including {words} in your message.'
            )

        help_message = '\n\n'.join(
            [f'@{user} here\'s what I can do.'] +
            lines +
            [f'To see how I work or contribute to development, check out {settings.GITHUB_LINK}.']
        )
        self.post(message['channel'], help_message)

    def show(self, message):
        """
        Post a message showing court availability. Include `tomorrow` in your message to look tomorrow.

        Arguments:
            message (dict): Message mentioning the bot which includes a 'show' trigger.
        """
        user = self.users[message['user']]
        text = message['text'].lower()

        tomorrow = 'tomorrow' in text
        when = 'tomorrow' if tomorrow else 'today'

        match = re.search(r'#(?P<number>\d)', text)
        number = int(match.group('number')) if match else None

        if number and not constants.COURTS.get(number):
            self.post(message['channel'], f'@{user} *#{number}* isn\'t a Z-Center court number.')
            return

        try:
            data = self.spider.availability(number=number, tomorrow=tomorrow)
        except:
            # flake8 thinks this variable is never used; it doesn't see it being
            # used in the f-string.
            date_string = slash_separated_date(tomorrow)  # noqa: F841
            logger.exception(f'Failed to retrieve court availability data for {date_string}.')

            self.post(message['channel'], f'@{user} something went wrong. Sorry!')
            return

        lines = []
        for court, hours in data.items():
            if hours:
                formatted_hours = [constants.HOURS[hour] for hour in hours]
                times = conversational_join(formatted_hours)

                lines.append(f'*#{court}* is available {when} at {times}.')

        if number:
            if lines:
                self.post(message['channel'], f'@{user} {lines[0]}')
            else:
                self.post(message['channel'], f'@{user} *#{number}* is not available {when}.')
        else:
            if lines:
                availability_message = '\n\n'.join(
                    [f'@{user} here\'s how the courts look.'] + lines
                )
                self.post(message['channel'], availability_message)
            else:
                self.post(message['channel'], f'@{user} there are no courts available {when}.')

    def book(self, message):
        """
        Book a court. Include a court number (e.g., #4) and a time to book (e.g., 8 PM) in your message.

        Arguments:
            message (dict): Message mentioning the bot which includes a 'book' trigger.
        """
        user = self.users[message['user']]
        text = message['text'].lower()

        tomorrow = 'tomorrow' in text
        when = 'tomorrow' if tomorrow else 'today'

        match = re.search(r'#(?P<number>\d)', text)
        number = int(match.group('number')) if match else None

        if not number:
            self.post(
                message['channel'],
                f'@{user} please give me a Z-Center court number to book (e.g., #1).'
            )
            return

        if not constants.COURTS.get(number):
            self.post(message['channel'], f'@{user} *#{number}* isn\'t a valid Z-Center court number.')
            return

        match = re.search(r'(?P<hour_string>\d+ (AM|PM))', text, re.IGNORECASE)
        hour_string = match.group('hour_string') if match else None

        if not hour_string:
            self.post(message['channel'], f'@{user} please give me a time to book (e.g., 8 PM).')
            return

        hour_string = hour_string.upper()
        hour = constants.HOUR_STRINGS.get(hour_string)
        if not hour:
            self.post(message['channel'], f'@{user} {hour_string} isn\'t a valid booking time.')
            return

        date_string = slash_separated_date(tomorrow)

        try:
            self.spider.book(number, hour, tomorrow=tomorrow)
        except AvailabilityError:
            logger.error(f'Court #{number} is not available at {hour_string} on {date_string}.')
            self.post(message['channel'], f'@{user} court #{number} isn\'t available at {hour_string} {when}.')
            return
        except:
            logger.exception(f'Failed to book court #{number} at {hour_string} on {date_string}.')
            self.post(message['channel'], f'@{user} something went wrong. Sorry!')
            return

        self.post(
            message['channel'],
            f'@{user} all set! I\'ve booked #{number} at {hour_string} {when}.'
        )

    def post(self, channel, text):
        """
        Post text to the given channel.

        See https://api.slack.com/methods/chat.postMessage.

        Arguments:
            channel (str): The channel to which to post.
            text (str): The message to post.
        """
        self.client.api_call(
            'chat.postMessage',
            as_user=True,
            channel=channel,
            link_names=True,
            text=text,
        )
