"""Authenticated UI adapter; the server supplies workspace and session scope."""
import argparse
import json

from pipelines.reviews.service import review
from server.database_scope import command_database

parser=argparse.ArgumentParser()
parser.add_argument('--workspace',required=True)
parser.add_argument('--config')
parser.add_argument('--session',required=True)
parser.add_argument('--request',default='{}')
args=parser.parse_args()
body=json.loads(args.request)
if body.get('action','status') not in ('status','continue','pause','instruction','retry'):
    raise ValueError('This action is not available from the workbench')
with command_database(args.config):
    print(json.dumps(review(args.workspace,args.session,body.get('action','status'),
        review_id=body.get('review_id'),instruction=body.get('instruction','')),ensure_ascii=False))
