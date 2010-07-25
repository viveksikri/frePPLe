#
# Copyright (C) 2007-2010 by Johan De Taeye
#
# This library is free software; you can redistribute it and/or modify it
# under the terms of the GNU Lesser General Public License as published
# by the Free Software Foundation; either version 2.1 of the License, or
# (at your option) any later version.
#
# This library is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU Lesser
# General Public License for more details.
#
# You should have received a copy of the GNU Lesser General Public
# License along with this library; if not, write to the Free Software
# Foundation Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA
#

# file : $URL$
# revision : $LastChangedRevision$  $LastChangedBy$
# date : $LastChangedDate$

from datetime import timedelta, datetime

from django.utils.translation import ugettext_lazy as _
from django.db.models import Min, Max
from django.db import connections, DEFAULT_DB_ALIAS

from freppledb.input.models import Parameter, Demand
from freppledb.output.models import DemandPegging, FlowPlan, LoadPlan
from freppledb.common.report import *
 
    
class ReportByDemand(ListReport):
  '''
  A list report to show peggings.
  '''
  template = 'output/pegging.html'
  title = _("Demand plan")
  reset_crumbs = False
  basequeryset = Demand.objects.all().values('name')
  frozenColumns = 0
  editable = False
  timebuckets = True
  javascript_imports = [       
    "/media/js/core.js",
    "/media/js/calendar.js",
    "/media/js/admin/DateTimeShortcuts.js",
    ]
  rows = (
    ('name', {   # XXX TODO
      'title': _('depth'),
      }),
    ('operation', {
      'title': _('operation'),
      }),
    ('buffer', {
      'title': _('buffer'),
      }),
    ('item', {
      'title': _('item'),
      }),
    ('resource', {
      'title': _('resource'),
      }),
    ('startdate', {
      'title': _('startdate'),
      }),
    ('enddate', {
      'title': _('enddate'),
      }),
    ('quantity', {
      'title': _('quantity'),
      }),
    ('percent used', {
      'title': _('% used'),
      }),
    )

  @staticmethod
  def resultlist1(request, basequery, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    basesql, baseparams = basequery.query.get_compiler(basequery.db).as_sql(with_col_aliases=True)
    cursor = connections[request.database].cursor()

    # query 1: pick up all resources loaded 
    resource = {}
    query = '''
      select operationplan_id, theresource
      from out_loadplan
      where operationplan_id in (
        select prod_operationplan as opplan_id
          from out_demandpegging
          where demand in (select dms.name from (%s) dms)
        union 
        select cons_operationplan as opplan_id
          from out_demandpegging
          where demand in (select dms.name from (%s) dms)
      )
      ''' % (basesql, basesql)
    cursor.execute(query, baseparams + baseparams)
    for row in cursor.fetchall():
      if row[0] in resource:
        resource[row[0]] += (row[1], )
      else:
        resource[row[0]] = ( row[1], )
     
    # query 2: pick up all operationplans
    query = '''    
      select min(depth), min(opplans.id), operation, opplans.quantity, 
        opplans.startdate, opplans.enddate, operation.name,
        max(buffer), max(opplans.item), opplan_id, out_demand.due,
        sum(quantity_demand) * 100 / opplans.quantity
      from (
        select depth, peg.id+1 as id, operation, quantity, startdate, enddate, 
          buffer, item, prod_operationplan as opplan_id, quantity_demand
        from out_demandpegging peg, out_operationplan prod
        where peg.demand in (select dms.name from (%s) dms)
        and peg.prod_operationplan = prod.id
        union
        select depth, peg.id, operation, quantity, startdate, enddate, 
          null, null, cons_operationplan, 0
        from out_demandpegging peg, out_operationplan cons
        where peg.demand in (select dms.name from (%s) dms)
        and peg.cons_operationplan = cons.id
      ) opplans
      left join operation 
      on operation = operation.name
      left join out_demand 
      on opplan_id = out_demand.operationplan
      group by operation, opplans.quantity, opplans.startdate, opplans.enddate, 
        operation.name, opplan_id, out_demand.due
      order by min(opplans.id)
      ''' % (basesql, basesql)
    cursor.execute(query, baseparams + baseparams)

    # Build the python result
    for row in cursor.fetchall():    
      yield {
          'depth': row[0],
          'peg_id': row[1],
          'operation': row[2],
          'quantity': row[3],
          'startdate': row[4],
          'enddate': row[5],
          'hidden': row[6] == None,
          'buffer': row[7],
          'item': row[8],
          'id': row[9],
          'due': row[10],
          'percent_used': row[11] or 100.0,
          'resource': row[9] in resource and resource[row[9]] or None,
          }


@staff_member_required
def GraphData(request, entity):
  basequery = Demand.objects.filter(name__exact=entity).values('name')
  try:
    current = datetime.strptime(Parameter.objects.using(request.database).get(name="currentdate").value, "%Y-%m-%d %H:%M:%S")
  except:
    current = datetime.now()
  (bucket,start,end,bucketlist) = getBuckets(request)  
  result = [ i for i in ReportByDemand.resultlist1(request,basequery,bucket,start,end) ]
  min = None
  max = None

  # extra query: pick up the linked operation plans  
  cursor = connections[request.database].cursor()
  query = '''
    select cons_operationplan, prod_operationplan
    from out_demandpegging
    where demand = '%s'
    group by cons_operationplan, prod_operationplan
    ''' % entity
  cursor.execute(query)
  links = [ {'to':row[1], 'from':row[0]} for row in cursor.fetchall() ]

  # Rebuild result list
  for i in result:
    if i['enddate'] < i['startdate'] + timedelta(1):
      i['enddate'] = i['startdate']
    else:
      i['enddate'] = i['enddate'] - timedelta(1)
    if i['startdate'] <= datetime(1971,1,1): i['startdate'] = current
    if i['enddate'] <= datetime(1971,1,1): i['enddate'] = current
    if min == None or i['startdate'] < min: min = i['startdate']  
    if max == None or i['enddate'] > max: max = i['enddate']
    if min == None or i['due'] and i['due'] < min: min = i['due']
    if max == None or i['due'] and i['due'] > max: max = i['due']

  # Add a line to mark the current date
  if min <= current and max >= current:
    todayline = current
  else:
    todayline = None

  # Snap to dates
  min = min.date()
  max = max.date() + timedelta(1)
  
  # Get the time buckets
  (bucket,start,end,bucketlist) = getBuckets(request, start=min, end=max)  
  buckets = []
  for i in bucketlist:
    if i['end'] >= min and i['start'] <= max:
      if i['end'] - timedelta(1) >= i['start']:
        buckets.append( {'start': i['start'], 'end': i['end'] - timedelta(1), 'name': i['name']} )
      else:
        buckets.append( {'start': i['start'], 'end': i['start'], 'name': i['name']} )
        
  context = { 
    'buckets': buckets, 
    'reportbucket': bucket,
    'reportstart': start,
    'reportend': end,
    'objectlist1': result, 
    'links': links,
    'todayline': todayline,
    }
  return HttpResponse(
    loader.render_to_string("output/pegging.xml", context, context_instance=RequestContext(request)),
    )

  
class ReportByBuffer(ListReport):
  '''
  A list report to show peggings.
  '''
  template = 'output/operationpegging.html'
  title = _("Pegging report")
  reset_crumbs = False
  basequeryset = FlowPlan.objects.all()
  frozenColumns = 0
  editable = False
  timebuckets = False
  rows = (
    ('operation', {
      'title': _('operation'),
      }),
    ('date', {
      'title': _('date'),
      }),
    ('demand', {
      'title': _('demand'),
      }),
    ('quantity', {
      'title': _('quantity'),
      }),
    )

  @staticmethod
  def resultlist1(request, basequery, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    cursor = connections[request.database].cursor()                           
    basesql, baseparams = basequery.query.where.as_sql(
      connections[DEFAULT_DB_ALIAS].ops.quote_name,
      connections[DEFAULT_DB_ALIAS])                              
    if not basesql: basesql = '1 = 1'
    
    query = '''    
        select operation, date, demand, quantity, due                                                        
        from                                                                                            
        (                                                                                               
        select out_demandpegging.demand, prod_date as date, operation, sum(quantity_buffer) as quantity, demand.due as due 
        from out_flowplan                                                                        
        join out_operationplan                                                                          
        on out_operationplan.id = out_flowplan.operationplan_id                                            
          and %s                                                        
          and out_flowplan.quantity > 0                                                               
        join out_demandpegging                                                                                 
        on out_demandpegging.prod_operationplan = out_flowplan.operationplan_id                            
        left join demand 
        on demand.name = out_demandpegging.demand
        group by demand, prod_date, operation, out_operationplan.id, demand.due                                     
        union                                                                                           
        select out_demandpegging.demand, cons_date as date, operation, -sum(quantity_buffer) as quantity, demand.due as due
        from out_flowplan                                                                               
        join out_operationplan                                                                          
        on out_operationplan.id = out_flowplan.operationplan_id                                            
          and %s                                                        
          and out_flowplan.quantity < 0                                                               
        join out_demandpegging                                                                          
        on out_demandpegging.cons_operationplan = out_flowplan.operationplan_id                            
        left join demand 
        on demand.name = out_demandpegging.demand
        group by demand, cons_date, operation, demand.due                                                           
        ) a                                                                                             
        order by demand, date, operation;                                                               
      ''' % (basesql, basesql)
    cursor.execute(query, baseparams + baseparams)

    # Build the python result
    for row in cursor.fetchall():    
      yield {
          'operation': row[0],
          'date': row[1],
          'demand': row[2],
          'quantity': row[3],
          'forecast': not row[4]
          }


class ReportByResource(ListReport):
  '''
  A list report to show peggings.
  '''
  template = 'output/operationpegging.html'
  title = _("Pegging report")
  reset_crumbs = False
  basequeryset = LoadPlan.objects.all()
  frozenColumns = 0
  editable = False
  timebuckets = False
  rows = (
    ('operation', {
      'title': _('operation'),
      }),
    ('date', {
      'title': _('date'),
      }),
    ('demand', {
      'title': _('demand'),
      }),
    ('quantity', {
      'title': _('quantity'),
      }),
    )

  @staticmethod
  def resultlist1(request, basequery, bucket, startdate, enddate, sortsql='1 asc'):
    # Execute the query
    cursor = connections[request.database].cursor()                           
    basesql, baseparams = basequery.query.where.as_sql(
      connections[DEFAULT_DB_ALIAS].ops.quote_name,
      connections[DEFAULT_DB_ALIAS])                              
    if not basesql: basesql = '1 = 1'
    
    query = '''    
        select operation, out_loadplan.startdate as date, out_demandpegging.demand, sum(quantity_buffer), demand.due
        from out_loadplan
        join out_operationplan
        on out_operationplan.id = out_loadplan.operationplan_id
          and %s
        join out_demandpegging
        on out_demandpegging.prod_operationplan = out_loadplan.operationplan_id
        left join demand 
        on demand.name = out_demandpegging.demand
        group by out_demandpegging.demand, out_loadplan.startdate, operation, demand.due
        order by out_demandpegging.demand, out_loadplan.startdate, operation
      ''' % (basesql)
    cursor.execute(query, baseparams)

    # Build the python result
    for row in cursor.fetchall():    
      yield {
          'operation': row[0],
          'date': row[1],
          'demand': row[2],
          'quantity': row[3],
          'forecast': not row[4]
          }

