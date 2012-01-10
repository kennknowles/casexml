def export_cases_and_referrals(cases, workbook, users=None):
    if users:
        by_user_id = dict([(user.user_id, user) for user in users])
    else:
        by_user_id = None
    case_static_keys = (
        "case_id",
        "username",
        "user_id",
        "type",
        "name",
        "opened_on",
        "modified_on",
        "closed",
        "closed_on",
        "domain",
        "external_id",
    )
    referral_keys = (
        "case_id",
        'referral_id',
        "type",
        "opened_on",
        "modified_on",
        "followup_on",
        "closed",
    )
    case_dynamic_keys = set()
    case_rows = []
    referral_rows = []

    def render_case_attr(case, key):
        attr = getattr(case, key)
        if isinstance (attr, dict):
            return attr.get('#text', '')
        else:
            return attr

    for case in cases:
        if not users or users and case.user_id in by_user_id:
            case_row = {'dynamic_properties': {}}
            for key in case_static_keys:
                if key == 'username':
                    try:
                        case_row[key] = by_user_id[case.user_id].raw_username
                    except TypeError:
                        case_row[key] = ''
                else:
                    case_row[key] = getattr(case, key)
            for key in case.dynamic_properties():
                case_dynamic_keys.add(key)
                case_row['dynamic_properties'][key] = render_case_attr(case, key)
            case_rows.append(case_row)

            for referral in case.referrals:
                referral_row = {}
                for key in referral_keys:
                    if key == "case_id":
                        referral_row[key] = case.case_id
                    else:
                        referral_row[key] = getattr(referral, key)
                referral_rows.append(referral_row)

    case_dynamic_keys = sorted(case_dynamic_keys)
    def tidy_up_case_row(case_row):
        row = dict([(key, case_row[key]) for key in case_static_keys])
        for key in case_dynamic_keys:

            row["d.{key}".format(key=key)] = case_row['dynamic_properties'].get(key, workbook.undefined)
        return row

    case_headers = list(case_static_keys)
    case_headers.extend(["d.{key}".format(key=key) for key in case_dynamic_keys])
    workbook.open("Case", case_headers)
    for case_row in case_rows:
        workbook.write_row("Case", tidy_up_case_row(case_row))
    workbook.open("Referral", referral_keys)
    for referral_row in referral_rows:
        workbook.write_row("Referral", referral_row)
    return workbook