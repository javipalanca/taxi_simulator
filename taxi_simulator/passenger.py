import json
import logging
import time

from spade.ACLMessage import ACLMessage
from spade.Agent import Agent
from spade.Behaviour import ACLTemplate, MessageTemplate, Behaviour

from utils import PASSENGER_WAITING, PASSENGER_IN_DEST, TAXI_MOVING_TO_PASSENGER, PASSENGER_IN_TAXI, \
    TAXI_IN_PASSENGER_PLACE, PASSENGER_LOCATION, PASSENGER_ASSIGNED, StrategyBehaviour
from protocol import REQUEST_PROTOCOL, TRAVEL_PROTOCOL, REQUEST_PERFORMATIVE, ACCEPT_PERFORMATIVE, REFUSE_PERFORMATIVE
from helpers import coordinator_aid, random_position, content_to_json

logger = logging.getLogger("PassengerAgent")


class PassengerAgent(Agent):
    def __init__(self, agentjid, password, debug):
        Agent.__init__(self, agentjid, password, debug=debug)
        self.agent_id = None
        self.status = PASSENGER_WAITING
        self.current_pos = None
        self.dest = None
        self.port = None
        self.taxi_assigned = None
        self.init_time = None
        self.waiting_for_pickup_time = None
        self.pickup_time = None
        self.end_time = None

        self.knowledge_base = {}

    def store_value(self, key, value):
        self.knowledge_base[key] = value

    def get_value(self, key):
        return self.knowledge_base.get(key)

    def has_value(self, key):
        return key in self.knowledge_base

    def _setup(self):
        try:
            tpl = ACLTemplate()
            tpl.setProtocol(TRAVEL_PROTOCOL)
            template = MessageTemplate(tpl)
            travel_behaviour = TravelBehaviour()
            self.addBehaviour(travel_behaviour, template)
            while not self.hasBehaviour(travel_behaviour):
                logger.warn("Passenger {} could not create TravelBehaviour. Retrying...".format(self.agent_id))
                self.addBehaviour(travel_behaviour, template)
        except Exception as e:
            logger.error("EXCEPTION creating TravelBehaviour in Passenger {}: {}".format(self.agent_id, e))

    def add_strategy(self, strategyClass):
        tpl = ACLTemplate()
        tpl.setProtocol(REQUEST_PROTOCOL)
        template = MessageTemplate(tpl)
        self.addBehaviour(strategyClass(), template)

    def set_id(self, agent_id):
        self.agent_id = agent_id

    def set_position(self, coords=None):
        if coords:
            self.current_pos = coords
        else:
            self.current_pos = random_position()
        logger.debug("Passenger {} position is {}".format(self.agent_id, self.current_pos))

    def get_position(self):
        return self.current_pos

    def set_target_position(self, coords=None):
        if coords:
            self.dest = coords
        else:
            self.dest = random_position()
        logger.debug("Passenger {} target position is {}".format(self.agent_id, self.dest))

    def is_in_destination(self):
        return self.status == PASSENGER_IN_DEST or self.get_position() == self.dest

    def total_time(self):
        if self.init_time and self.end_time:
            return self.end_time - self.init_time
        else:
            return 0

    def get_waiting_time(self):
        if self.init_time:
            if self.pickup_time:
                t = self.pickup_time - self.init_time
            else:
                t = time.time() - self.init_time
            return t
        return None

    def get_pickup_time(self):
        if self.pickup_time:
            return self.pickup_time - self.waiting_for_pickup_time
        return None

    def to_json(self):
        t = self.get_waiting_time()
        return {
            "id": self.agent_id,
            "position": self.current_pos,
            "dest": self.dest,
            "status": self.status,
            "taxi": self.taxi_assigned,
            "url": "http://127.0.0.1:{port}".format(port=self.port),
            "waiting": float("{0:.2f}".format(t)) if t else None
        }


class TravelBehaviour(Behaviour):
    def onStart(self):
        logger.debug("Passenger {} started TravelBehavior.".format(self.myAgent.agent_id))

    def _process(self):
        try:
            msg = self._receive(block=True)
            if msg:
                content = content_to_json(msg)
                logger.debug("Passenger {} informed of: {}".format(self.myAgent.agent_id, content))
                if "status" in content:
                    status = content["status"]
                    if status != 23:
                        logger.info("Passenger {} informed of status: {}".format(self.myAgent.agent_id, status))
                    if status == TAXI_MOVING_TO_PASSENGER:
                        logger.info("Passenger {} waiting for taxi.".format(self.myAgent.agent_id))
                        self.myAgent.waiting_time = time.time()
                    elif status == TAXI_IN_PASSENGER_PLACE:
                        self.myAgent.status = PASSENGER_IN_TAXI
                        logger.info("Passenger {} in taxi.".format(self.myAgent.agent_id))
                        self.myAgent.pick_up_time = time.time()
                    elif status == PASSENGER_IN_DEST:
                        self.myAgent.status = PASSENGER_IN_DEST
                        self.myAgent.end_time = time.time()
                        logger.info("Passenger {} arrived to destiny after {} seconds.".format(self.myAgent.agent_id,
                                                                                               self.myAgent.total_time()))
                    elif status == PASSENGER_LOCATION:
                        coords = content["location"]
                        self.myAgent.set_position(coords)
        except Exception as e:
            logger.error("EXCEPTION in Travel Behaviour of Passenger {}: {}".format(self.myAgent.agent_id, e))


class PassengerStrategyBehaviour(StrategyBehaviour):
    def onStart(self):
        self.logger = logging.getLogger("PassengerAgent")
        self.logger.debug("Strategy {} started in passenger {}".format(type(self).__name__, self.myAgent.agent_id))
        self.myAgent.init_time = time.time()

    def send_request(self, content=None):
        """
        Sends an :class:`ACLMessage` to the coordinator to request a taxi.
        It uses the REQUEST_PROTOCOL and the REQUEST_PERFORMATIVE.
        If no content is set a default content with the passenger_id,
        origin and target coordinates is used.
        :param content: Optional content dictionary
        :type content: :class:`dict`
        """
        if content is None or len(content) == 0:
            content = {
                "passenger_id": self.myAgent.agent_id,
                "origin": self.myAgent.current_pos,
                "dest": self.myAgent.dest
            }
        if not self.myAgent.dest:
            self.myAgent.dest = random_position()
        msg = ACLMessage()
        msg.addReceiver(coordinator_aid)
        msg.setProtocol(REQUEST_PROTOCOL)
        msg.setPerformative(REQUEST_PERFORMATIVE)
        msg.setContent(json.dumps(content))
        self.myAgent.send(msg)
        self.logger.info("Passenger {} asked for a taxi to {}.".format(self.myAgent.agent_id, self.myAgent.dest))

    def accept_taxi(self, taxi_aid):
        """
        Sends an :class:`ACLMessage` to a taxi to accept a travel proposal.
        It uses the REQUEST_PROTOCOL and the ACCEPT_PERFORMATIVE.
        :param taxi_aid: The AgentID of the taxi
        :type taxi_aid: :class:`spade.AID.aid`
        """
        reply = ACLMessage()
        reply.addReceiver(taxi_aid)
        reply.setProtocol(REQUEST_PROTOCOL)
        reply.setPerformative(ACCEPT_PERFORMATIVE)
        content = {
            "passenger_id": self.myAgent.agent_id,
            "origin": self.myAgent.current_pos,
            "dest": self.myAgent.dest
        }
        reply.setContent(json.dumps(content))
        self.myAgent.send(reply)
        self.myAgent.taxi_assigned = taxi_aid.getName()
        self.logger.info("Passenger {} accepted proposal from taxi {}".format(self.myAgent.agent_id,
                                                                               taxi_aid.getName()))

    def refuse_taxi(self, taxi_aid):
        """
        Sends an ACLMessage to a taxi to refuse a travel proposal.
        It uses the REQUEST_PROTOCOL and the REFUSE_PERFORMATIVE.
        :param taxi_aid: The AgentID of the taxi
        :type taxi_aid: :class:`spade.AID.aid`
        """
        reply = ACLMessage()
        reply.addReceiver(taxi_aid)
        reply.setProtocol(REQUEST_PROTOCOL)
        reply.setPerformative(REFUSE_PERFORMATIVE)
        content = {
            "passenger_id": self.myAgent.agent_id,
            "origin": self.myAgent.current_pos,
            "dest": self.myAgent.dest
        }
        reply.setContent(json.dumps(content))
        self.myAgent.send(reply)
        self.logger.info("Passenger {} refused proposal from taxi {}".format(self.myAgent.agent_id,
                                                                              taxi_aid.getName()))

    def _process(self):
        raise NotImplementedError
