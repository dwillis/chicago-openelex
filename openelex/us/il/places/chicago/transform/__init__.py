from datetime import datetime
import re
import probablepeople as pp

from openelex.base.transform import Transform, registry
from openelex.models import Candidate, Contest, Office, Party, RawResult, Result

STATE = 'IL'
PLACE = 'Chicago'
COUNTY = 'Cook'


meta_fields = ['source', 'election_id', 'state', 'place']

contest_fields = meta_fields + ['start_date',
                                'end_date',
                                'election_type',
                                'primary_type',
                                'result_type',
                                'special',
                                ]
candidate_fields = meta_fields + ['full_name', 'given_name',
                                  'family_name', 'additional_name']
result_fields = meta_fields + ['reporting_level', 'jurisdiction',
                               'votes', 'total_votes', 'vote_breakdowns']


class BaseTransform(Transform):

    # these are offices where we have to parse district
    # out of the office name
    district_offices = set([
        'U.S. Senate',
        'U.S. House',
        'State Senate',
        'State House',
        'County Commissioner'
    ])

    def __init__(self):
        super(BaseTransform, self).__init__()
        self._office_cache = {}
        self._contest_cache = {}

    def get_raw_results(self):
        return RawResult.objects.filter(state=STATE, place=PLACE).no_cache()

    def get_judge_candidate_fields(self, raw_result):
        fields = self._get_fields(raw_result, candidate_fields)
        fields['full_name'] = None
        return fields

    def get_candidate_fields(self, raw_result):
        fields = self._get_fields(raw_result, candidate_fields)
        full_name = raw_result.full_name.strip()

        if full_name.lower() in ['yes', 'no']:
            fields = self.get_judge_candidate_fields(raw_result)
            return fields

        if full_name.lower() in ['no candidate', 'candidate withdrew']:
            fields['full_name'] = None
            return fields

        try:
            name_parts, name_type = pp.tag(full_name)

            if name_type != 'Person':
                print "***************************"
                print "NOT A PERSON:", fields['full_name']
                print "fields:", fields
                print "tagged name:", name_parts
                print "***************************"
                fields['full_name'] = full_name
                return fields

            fields['given_name'] = name_parts.get('GivenName')
            fields['family_name'] = name_parts.get('Surname')
            if 'SuffixGenerational' in name_parts:
                fields['suffix'] = name_parts['SuffixGenerational']
            if 'Nickname' in name_parts:
                fields['additional_name'] = name_parts['Nickname']

            fields['full_name'] = full_name

        except pp.RepeatedLabelError:
            print "***************************"
            print "UNABLE TO TAG:", full_name
            print "***************************"
            fields['full_name'] = full_name

        return fields

    def _get_fields(self, raw_result, field_names):
        return {k: getattr(raw_result, k) for k in field_names}

    def get_contest(self, raw_result):
        """
        Returns the Contest model instance for a given RawResult.

        Caches the result in memory to reduce the number of calls to the
        datastore.
        """
        key = "%s-%s" % (raw_result.election_id, raw_result.contest_slug)

        try:
            return self._contest_cache[key]
        except KeyError:
            fields = self.get_contest_fields(raw_result)

            if fields and fields['office']:
                fields.pop('source')
                try:
                    try:
                        contest = Contest.objects.filter(**fields)[0]
                    except IndexError:
                        contest = Contest.objects.get(**fields)
                except Exception:
                    print fields
                    print "\n"
                    raise
                self._contest_cache[key] = contest
                return contest
            else:
                self._contest_cache[key] = None
                return None

    def get_contest_fields(self, raw_result):
        fields = self._get_fields(raw_result, contest_fields)
        office = self._get_or_make_office(raw_result)
        if office:
            fields['office'] = office
            return fields
        else:
            return None

    def _get_or_make_office(self, raw_result):
        clean_name = self._clean_office_name(raw_result.office)

        if clean_name:

            office_query = self._make_office_query(clean_name, raw_result)
            key = Office.make_key(**office_query)

            try:
                return self._office_cache[key]
            except KeyError:
                try:
                    office = Office.objects.get(**office_query)
                    self._office_cache[key] = office
                    return office
                except Office.DoesNotExist:
                    office = Office(**office_query)
                    office.save()
                    self._office_cache[key] = office
                    return office
        else:
            return None

    def _clean_office_name(self, office):
        """
        See: https://github.com/openelections/core/blob/dev/openelex/us/wa/load.py#L370

        """

        us_pres =       ('president.+united\sstates|pres\sand\svice\spres', 
                        'President')
        us_senator =    ('senator.+u\.s\.|u\.s\..+senator|united\sstates\ssenator',
                        'U.S. Senate')
        us_rep =        ('u\.s\.\srepresentative|rep.+in\scongress',
                        'U.S. House')

        state_senator = ('state\ssenator',
                        'State Senate')
        state_rep =     ('state\srepresentative|rep.+gen.+assembly',
                        'State House')
        gov_lt_gov =    ('governor.+lieutenant\sgovernor',
                        'Governor & Lieutenant Governor')
        lt_gov =        ('lieutenant\sgovernor',
                        'Lieutenant Governor')
        gov =           ('governor',
                        'Governor')
        sec_state =     ('secretary',
                        'Secretary of State')
        aty_gen =       ('attorney\sgeneral',
                        'Attorney General')
        state_aty =     ('state.+attorney',
                        'State\'s Attorney')
        comptroller =   ('comptroller',
                        'Comptroller')
        county_treas =  ('county.+treasurer|treasurer.+county',
                        'County Treasurer') # should 'County' be in the office name?
        state_treas =   ('treasurer',
                        'Treasurer')

        # should 'County' be in the office name?
        county_board_pres = ('board.+pres.+county|county.+board.+pres|pres.+county.+board',
                        'County Board President')
        county_board_comm = ('county.+comm|comm.+county',
                        'County Commissioner')
        sheriff =       ('sheriff',
                        'County Sheriff')
        assessor =      ('assessor',
                        'County Assessor')
        rec_deeds =     ('deeds',
                        'County Recorder of Deeds')
        cir_ct_clerk =  ('circuit.+clerk|clerk.+circuit',
                        'County Circuit Court Clerk')
        clerk =         ('clerk',
                        'County Clerk')

        supreme_ct =    ('supreme\scourt',
                        'Supreme Court Judge')
        appellate_ct =  ('app?ellate\scourt',
                        'Appellate Court Judge')
        subcircuit_ct = ('judge.+circuit.+\d|judge.+\d.+sub|circuit.+court.+\d.+sub|judge.+subcircuit',
                        'Circuit Court Judge')
        circuit_ct_full = ('circuit.+judge|judge.+circuit',
                        'Circuit Court Judge')

        mayor =         ('mayor',
                        'Mayor')
        alderman =      ('alderman',
                        'Alderman')
        committeeman =  ('committeeman',
                        'Ward Committeeman')

        # the order of searches matters (b/c of overlapping keywords)
        office_searches = [us_pres, us_senator, us_rep, state_senator, state_rep,
                          gov_lt_gov, lt_gov, gov, sec_state, aty_gen, state_aty, comptroller,
                          county_treas, state_treas, county_board_pres, county_board_comm,
                          sheriff, assessor, rec_deeds, cir_ct_clerk, clerk,
                          supreme_ct, appellate_ct, subcircuit_ct, circuit_ct_full,
                          mayor, alderman, committeeman]

        for srch_regex, clean_office_name in office_searches:
            if re.search(srch_regex, office):
                return clean_office_name

        return None

    def _make_office_query(self, office_name, raw_result):
        """
        Gets the right state, place, district for an office
        """

        office_query = {
            'name': office_name,
            'state': STATE
        }
        office_name_raw = raw_result.office

        if office_name == 'President':
            office_query['state'] = 'US'

        if office_name in self.district_offices:
            if re.findall("\d+", office_name_raw):
                office_query['district'] = re.findall("\d+", office_name_raw)[0]
            else:
                office_query['district'] = None
        if office_name == 'Circuit Court Judge':
            if re.findall("\d+", office_name_raw):
                office_query['district'] = 'Subcircuit '+re.findall("\d+", office_name_raw)[0]
            else:
                office_query['district'] = None

        if office_name in ['Mayor', 'Alderman', 'Ward Committeeman']:
            office_query['place'] = PLACE

        if office_name in ['Alderman', 'Ward Committeeman']:
            if re.findall("\d+", office_name_raw):
                office_query['district'] = 'Ward '+re.findall("\d+", office_name_raw)[0]

        if re.search('county', office_name_raw) and 'judge' not in office_name.lower():
            office_query['county'] = COUNTY

        return office_query


class CreateContestsTransform(BaseTransform):
    name = 'chicago_create_unique_contests'

    def __call__(self):
        contests = []
        seen = set()

        for result in self.get_raw_results():
            key = self._contest_key(result)
            if key not in seen:
                fields = self.get_contest_fields(result)
                if fields:
                    fields['updated'] = datetime.now()
                    fields['created'] = datetime.now()
                    contest = Contest(**fields)
                    print "   %s" %contest
                    contests.append(contest)
                    seen.add(key)

        Contest.objects.insert(contests, load_bulk=False)

    def _contest_key(self, raw_result):
        slug = raw_result.contest_slug
        return (raw_result.election_id, slug)

    def reverse(self):
        old = Office.objects.filter(state=STATE)
        print "\tDeleting %d previously created offices" % old.count()
        old.delete()

        old = Contest.objects.filter(state=STATE)
        print "\tDeleting %d previously created contests" % old.count()
        old.delete()


class CreateCandidatesTransform(BaseTransform):
    name = 'chicago_create_unique_candidates'

    def __init__(self):
        super(CreateCandidatesTransform, self).__init__()

    def __call__(self):
        candidates = []
        seen = set()

        for rr in self.get_raw_results():
            key = (rr.election_id, rr.contest_slug, rr.candidate_slug)
            if key not in seen:

                fields = self.get_candidate_fields(rr)

                if fields['full_name']:
                    contest = self.get_contest(rr)
                    if contest:
                        fields['contest'] = contest
                        candidate = Candidate(**fields)
                        candidates.append(candidate)

                seen.add(key)


        Candidate.objects.insert(candidates, load_bulk=False)

    def reverse(self):
        old = Candidate.objects.filter(state=STATE)
        print "\tDeleting %d previously created candidates" % old.count()
        old.delete()


class CreateResultsTransform(BaseTransform): 
    name = 'chicago_create_unique_results'

    auto_reverse = True

    def __init__(self):
        super(CreateResultsTransform, self).__init__()
        self._candidate_cache = {}

    def __call__(self):
        results = []

        # for now, skip offices that don't have candidates populated
        # e.g. retaining judges, ballot initiatives
        office_to_skip = None
        for rr in self.get_rawresults():
            this_office = rr.election_id+rr.office

            if this_office != office_to_skip:
                if rr.full_name.strip().lower() in ['yes', 'no', 'no candidate', 'candidate withdrew']:
                    office_to_skip = this_office
                    pass
                else:
                    fields = self._get_fields(rr, result_fields)
                    fields['contest'] = self.get_contest(rr)
                    if fields['contest']:
                        try:
                            fields['candidate'] = self.get_candidate(rr, extra={
                                'contest': fields['contest'],
                            })
                            fields['contest'] = fields['candidate'].contest 
                            fields['raw_result'] = rr

                            result = Result(**fields)
                            results.append(result)
                        except Candidate.MultipleObjectsReturned:
                            print "*"*50
                            print "multiple candidates returned"
                            print "fields: %s" %fields

                    # for now, add results in chunks
                    # instead of all at once at the end
                    if len(results) >= 1000:
                        self._create_results(results)
                        results = []

        self._create_results(results)

    def get_results(self):
        election_ids = self.get_rawresults().distinct('election_id')
        return Result.objects.filter(election_id__in=election_ids)

    def get_rawresults(self):
        return RawResult.objects

    def get_candidate(self, raw_result, extra={}):
        """
        Get the Candidate model for a RawResult

        Keyword arguments:

        * extra - Dictionary of extra query parameters that will
                  be used to select the candidate. 
        """
        key = (raw_result.election_id, raw_result.contest_slug,
            raw_result.candidate_slug)
        try:
            return self._candidate_cache[key]
        except KeyError:
            fields = self.get_candidate_fields(raw_result)
            fields.update(extra)
            del fields['source']
            try:
                candidate = Candidate.objects.get(**fields)
            except Candidate.DoesNotExist:
                raise
            self._candidate_cache[key] = candidate 
            return candidate

    def _create_results(self, results):
        """
        Create the Result objects in the database.
        """
        Result.objects.insert(results, load_bulk=False)
        print "Created %d results." % len(results)

    def reverse(self):
        old_results = self.get_results()
        print "\tDeleting %d previously loaded results" % old_results.count() 
        old_results.delete()


registry.register('il', CreateContestsTransform)
registry.register('il', CreateCandidatesTransform)
registry.register('il', CreateResultsTransform)
