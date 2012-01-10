from couchdbkit.ext.django.schema import *
from dimagi.utils.mixins import UnicodeMixIn
from casexml.apps.case import const
from casexml.apps.case.sharedmodels import CommCareCaseIndex, IndexHoldingMixIn

class User(object):
    """
    This is a basic user model that's used for OTA restore to properly
    find cases and generate the user XML.
    """
    
    def __init__(self, user_id, username, password, date_joined, user_data=None):
        self.user_id = user_id
        self.username = username
        self.password = password
        self.date_joined = date_joined
        self.user_data = user_data or {}
    
    def get_case_updates(self, last_sync):
        """
        Get open cases associated with the user. This method
        can be overridden to change case-syncing behavior
        
        returns: list of (CommCareCase, previously_synced) tuples
        """
        from casexml.apps.phone.caselogic import get_case_updates
        return get_case_updates(self, last_sync)
    
    @classmethod
    def from_django_user(cls, django_user):
        return cls(user_id=str(django_user.pk), username=django_user.username,
                   password=django_user.password, date_joined=django_user.date_joined,
                   user_data={})
    

class CaseState(DocumentSchema, IndexHoldingMixIn):
    """
    Represents the state of a case on a phone.
    """
    
    case_id = StringProperty()
    indices = SchemaListProperty(CommCareCaseIndex)
        

class SyncLog(Document, UnicodeMixIn):
    """
    A log of a single sync operation.
    """
    
    date = DateTimeProperty()
    user_id = StringProperty()
    previous_log_id = StringProperty()  # previous sync log, forming a chain
    last_seq = IntegerProperty()        # the last_seq of couch during this sync
    
    # cases that were synced down during this sync
    cases = StringListProperty()
    
    # we need to store a mapping of cases to indices for generating the footprint
    
    # The cases_on_phone property represents the state of all cases the server thinks
    # the phone has on it and cares about. 
    
    # The dependant_cases_on_phone property represents the possible list of cases
    # also on the phone because they are referenced by a real case's index (or 
    # a dependent case's index). This list is not necessarily a perfect reflection
    # of what's on the phone, but is guaranteed to be after pruning
    cases_on_phone = SchemaListProperty(CaseState)
    dependent_cases_on_phone = SchemaListProperty(CaseState)
    owner_ids_on_phone = StringListProperty()
    
    @classmethod
    def last_for_user(cls, user_id):
        return SyncLog.view("phone/sync_logs_by_user", 
                            startkey=[user_id, {}],
                            endkey=[user_id, ""],
                            descending=True,
                            limit=1,
                            reduce=False,
                            include_docs=True).one()

    def get_previous_log(self):
        """
        Get the previous sync log, if there was one.  Otherwise returns nothing.
        """
        if not hasattr(self, "_previous_log_ref"):
            self._previous_log_ref = SyncLog.get(self.previous_log_id) if self.previous_log_id else None
        return self._previous_log_ref
    
    def _walk_the_chain(self, func):
        """
        Given a function that takes in a log and returns a list, 
        walk up the chain to extend the list by calling the function
        on all parents.
        
        Returns a set object, stripping all duplicate ids
        
        Used to generate case id lists for synced, purged, and other cases
        """
        chain = set(func(self))
        previous_log = self.get_previous_log()
        if previous_log:
            chain = chain | previous_log._walk_the_chain(func)
        return chain
    
    def _init_casemap_if_necessary(self):
        if not hasattr(self, "_cached_casemap"):
            self._cached_casemap = dict((case.case_id, case) for case in self.cases_on_phone)
        
    def phone_has_case(self, case_id):
        """
        Whether the phone currently has a case, according to this sync log
        """
        return self.get_case_state(case_id) is not None
        
    def get_case_state(self, case_id):
        """
        Get the case state object associated with an id, or None if no such
        object is found
        """
        filtered_list = [case for case in self.cases_on_phone if case.case_id == case_id]
        if filtered_list:
            assert(len(filtered_list) == 1, 
                   "Should be exactly 0 or 1 cases on phone but were %s for %s" %
                   (len(filtered_list), case_id))
            return filtered_list[0]
        return None
    
    def phone_has_dependent_case(self, case_id):
        """
        Whether the phone currently has a dependent case, according to this sync log
        """
        return self.get_dependent_case_state(case_id) is not None
        
    def get_dependent_case_state(self, case_id):
        """
        Get the dependent case state object associated with an id, or None if no such
        object is found
        """
        filtered_list = [case for case in self.dependent_cases_on_phone if case.case_id == case_id]
        if filtered_list:
            assert(len(filtered_list) == 1, 
                   "Should be exactly 0 or 1 dependent cases on phone but were %s for %s" %
                   (len(filtered_list), case_id))
            return filtered_list[0]
        return None
    
    def archive_case(self, case_id):
        state = self.get_case_state(case_id)
        self.cases_on_phone.remove(state)
        self.dependent_cases_on_phone.append(state)
    def get_all_cases_seen(self):
        """
        All cases the phone has ever seen.
        Union of:
         - any case previously synced.
         - any case that has ever been submitted to.
        """
        raise NotImplementedError("This is broken!")
    
    def get_open_cases_on_phone(self):
        """
        The current list of open cases on the phone.
        The formula is:
         - Cases synced down PLUS cases submitted by phone 
           MINUS (cases closed by phone PLUS cases already purged) 
        """
        ret = self.get_all_cases_seen() 
        # TODO: Asserts? Anything in the latter two sets
        # should have already been in the original list
        ret = ret - self.get_closed_case_ids()
        return ret
    
    def update_phone_lists(self, xform, case_list):
        # for all the cases update the relevant lists in the sync log
        # so that we can build a historical record of what's associated
        # with the phone
                
        for case in case_list:
            actions = case.get_actions_for_form(xform.get_id)
            for action in actions:
                try: 
                    if action.action_type == const.CASE_ACTION_CREATE:
                        assert(not self.phone_has_case(case.get_id))
                        self.cases_on_phone.append(CaseState(case_id=case.get_id, 
                                                             indices=[]))
                    elif action.action_type == const.CASE_ACTION_UPDATE:
                        assert(self.phone_has_case(case.get_id))
                        # no actual action necessary here
                    elif action.action_type == const.CASE_ACTION_INDEX:
                        assert(self.phone_has_case(case.get_id))
                        # reconcile indices
                        case_state = self.get_case_state(case.get_id)
                        case_state.update_indices(action.indices)
                    elif action.action_type == const.CASE_ACTION_CLOSE:
                        assert(self.phone_has_case(case.get_id))
                        self.archive_case(case.get_id)
                except Exception, e:
                    # debug
                    # import pdb
                    # pdb.set_trace()
                    raise    
        self.save()
            
    def __unicode__(self):
        return "%s synced on %s (%s)" % (self.chw_id, self.date.date(), self.get_id)

from casexml.apps.phone import signals