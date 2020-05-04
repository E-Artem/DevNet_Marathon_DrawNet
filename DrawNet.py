
from nornir import InitNornir
from nornir.plugins.tasks.networking import netmiko_send_command
from datetime import datetime
import yaml, json, os, re
import graphviz as gv

templates = os.path.dirname(os.path.abspath(__file__)) + '\\ntc-template\\templates\\'
os.environ['NET_TEXTFSM']= templates

WORK_DIR = os.path.dirname(os.path.realpath(__file__))
SCHM_DIR = os.path.join(WORK_DIR,"schemas\\")                                             # directory fullpath for topology schemas

uni_mac = lambda x: re.sub(r'[ .:]', "", x.lower())                                     # Лямбда исключения разделителей в mac и приведения к нижнему регистру
l_nodes = list()                                                                        # Инициализация списка узлов
lastnode = 0                                                                            # идентификатор последнего добавленного устройства (0, если устройств нет) 
devs = list()                                                                           # список соответствий вида dev_name - id_node - uuid - caps (dev_name - из hosts.yaml, id_node - id устройства в l_nodes) 
l_lines = list()                                                                        # Инициализация списка связей между устройствами

def main():

    str_fname = "topology_{}".format(datetime.now().strftime("%Y%m%d-%H%M%S"))
    if not os.path.exists(SCHM_DIR): os.mkdir(SCHM_DIR)                                 # Создание рабочей директории (если не существует) для топологий схем

    nr = InitNornir(config_file="config.yaml")
    res_sver = nr.run(netmiko_send_command, command_string="show version", use_textfsm=True)
    res_slnd = nr.run(netmiko_send_command, command_string="show lldp neighbors detail", use_textfsm=True)

    process_show_version_info(res_sver)                                                 # Обработка данных вывода команды show version. 
                                                                                        #  формирование первых записей в структурах данных узлов l_nodes и устройств devs 
    process_lldp_info(res_slnd)                                                         # Обработка данных вывода команды show lldp neighbor detail. 
                                                                                        #  формирование дополнительных записей в структурах l_nodes, devs по данным LLDP
                                                                                        #  формирование списка связей между устройствами l_lines
    save_and_diff_topology(str_fname)

    draw_topology(str_fname)                                                            # Создать графический файл с топологией на основе собранных данных    

    None

def get_uuid_from_maclist(mac_list):                                                    # Возвращает уникальный идентификатор устройства на основе данных о его mac
    l_work = list()                                                                     #  инициализация l_work
    for x in mac_list: l_work.append(uni_mac(x))                                        #  формируем новый список из приведенных значений mac-адресов
    # l_work.sort()                                                                     #  сортируем список (уникальность)
    return ", ".join(l_work)                                                            #  возвращаем строку с разделителями из отсортированного приведенного списка значений mac-адресов

def process_show_version_info(res_sver):                                                # Формируем элементы списка узлов на основе вывода "show version" с устройств
  global lastnode
  if not res_sver.failed:                                                               # Если не было ошибки в процессе сбора данных "show version" с устройств
    for dev in res_sver:                                                                #  для блока данных dev каждого устройста 
        if not res_sver[dev].failed:                                                    #   если данные в блоке dev успешно заполнены
            pt = lambda x: res_sver[dev][0].result[0][x]                                #    определение лямбды доступа к данным разбираемого устройства
            if len(pt('mac')) > 0:                                                      #    если устройство описывает однин или несколько (например, стек) узлов
                fl_succ = True                                                          #    инициализируем флаг успешного добавления узлов разбираемого устройства
                for i in range (0, len(pt('mac'))):                                     #     для всех узлов устройства (если есть стек их больше 1)
                    if not [n for n, x in enumerate(l_nodes) if x['mac'] == pt('mac')]: #      если в списке узлов нет элемента с mac текущего узла
                        l_nodes.append({                                                #       добавить новый узел и его параметры в список узлов
                            'id': lastnode + 1,                                         #       (добавляются узлы на основе списка устройств host.yaml)
                            'dev': dev,
                            'mac': uni_mac(pt('mac')[i]),
                            'hostname': pt('hostname'),
                            'hardware': pt('hardware')[i],
                            'serial': pt('serial')[i] })        
                        print("New Node:\n Device ID {}: host {} (MAC {}; SN '{}'), HW {}\n".format(   #       вывести на экран параметры добавляемого узла
                            lastnode + 1, pt('hostname'), uni_mac(pt('mac')[i]),
                            pt('serial')[i], pt('hardware')[i]))                                
                    else:                                                               #      если в списке узлов есть элемент с mac текущего узла (если устройство уже встречалось)
                        fl_succ = False                                                 #       cбросить флаг успешного добавления узлов разбираемого устройства
                        break                                                           #       досрочный выход из цикла
                if fl_succ:                                                             #     если все узлы разбираемого устройства были добавлены 
                    lastnode += 1                                                       #      формируем идентификатор для следующего устройства
                    uuid = get_uuid_from_maclist(pt('mac'))                             #      вычисляем уникальный идентификатор устройства на основе mac его узлов
                    devs.append({                                                       #      добавляем элемент соответствия dev_name - id_node - uuid - capablilities(будут заполнены позже)
                        'dev_name': dev,
                        'hostname': pt('hostname'),
                        'id_node': lastnode,
                        'uuid': uuid,
                        'caps': ""})

def process_lldp_info(res_slnd):
  global lastnode
  if not res_slnd.failed:                                                               # Если не было ошибки в процессе сбора данных "show lldp neighbors detail" с устройств
    for dev in res_slnd:                                                                #  для блока данных dev каждого устройста 
        if not res_slnd[dev].failed:                                                    #   если данные в блоке dev успешно заполнены
            for lnk in res_slnd[dev][0].result:                                         #     анализируем lldp-запись
                ar = [x for x in devs if x['dev_name'] == dev]                          #     находим запись соответствия по имени устройства (из hosts.yaml)
                if len(ar) == 1:                                                        #     если соответствие (dev_name - id_node - uuid) найдено
                    a_id = ar[0]['id_node']                                             #     a_id - идентификатор устройства id из списка узлов l_nodes  
                    a_uuid = ar[0]['uuid']                                              #     a_uuid - uuid-идентификатор устройства a (с которого разбирается строка lldp)
                    a_port = lnk['local_interface']                                     #     a_port - Port ID интерфейса устройства a

                    an = [y for y in l_nodes if y['id'] == a_id]                        #     по идентификатору устройства из списка узлов l_nodes находим соответствующий узел (узлы)
                    if len(an) == 1:                                                    #     если узел один (нет стека)
                        a_hostname = an[0]['hostname']                                  #       инициализируем переменную a_hostname устройства a
                        a_serialnum = an[0]['serial']                                   #       инициализируем переменную a_hostname устройства a
                        a_hw = re.sub(r'\,.*$',"",an[0]['hardware'])                    #       инициализируем переменную a_hw (значение слева до первой запятой из поля HW LLDP)
                    elif len(an) > 1:                                                   #     если узлов несколько (стек)
                        a_hostname = an[0]['hostname']                                  #       инициализируем переменную a_hostname устройства а именем первого узла
                        sn = []                                                         #       инициализируем переменную a_serial списком серийников с разделителем
                        for i in range (len(an)): sn.append(an[i]['serial'])
                        a_serialnum = ", ".join(sn)
                        a_hw = "{} devices".format(len(an))                             #       вместо модели сообщаем количество устройств

                    if ('B,T' in lnk['capabilities']):                                  # Если это IP-телефон
                        b_uuid = re.sub(r':.*$',"",lnk['neighbor_port_id']).lower()     #   mac для uuid извлекаем из neighbor_port_id
                        b_port = re.sub(r'.*:',"",lnk['neighbor_port_id'])              #   Port ID
                    else:                                                               # В противном случае mac для uuid берется из chassis_id
                        b_uuid = get_uuid_from_maclist([lnk['chassis_id']])
                        b_port = lnk['neighbor_port_id']
                        
                    b_hostname = re.sub(r'\..*$',"",lnk['neighbor'])                    # Из FQDN берем hostname
                    b_serialnum = lnk['serial']

                    l_nodes_i = [n for n,x in enumerate(l_nodes) if b_uuid in x['mac']] # Попытаться найти по mac устройство из списка узлов устройств (составленного по hosts.yaml, дополняемого по LLDP)
                    if len(l_nodes_i) > 0:                                              # Если таковое устройство найдено

                        seek_id = l_nodes_i[0]                                          #  идентификатор устройства; для поиска индекса элемента (dev_name - id_node - uuid - caps) списка соответствий
                        for i in range (len(devs)):                                     #  поиск в списке devs элемента, в котором id_node = seek_id + 1 (корректировка индекса)
                            if devs[i]['id_node'] == seek_id + 1:                       #   Если таковой элемент найден
                                b_uuid = devs[i]['uuid']                                #   корректировка b_uuid (значение может содержать идентификатор устройства из нескольких узлов стека)
                                b_id = devs[i]['id_node']
                                devs[i]['caps'] = lnk['capabilities']                   #   значение caps элемента (dev_name - id_node - uuid - caps) заполняется LLDP-данными с другого устройства на него
                                break                                                   #   досрочный выходи из цикла
                       
                        an = [n for n,y in enumerate(l_nodes) if y['id'] == b_id]       #  по идентификатору устройства (найденного ранее по mac) ищем все его узлы 
                        if len(an) == 1:                                                #   если узел один
                            b_serialnum = l_nodes[an[0]]['serial']
                            b_hw = re.sub(r'\,.*$',"",l_nodes[an[0]]['hardware'])       #   инициализируем переменную a_hw (значение слева до первой запятой из поля HW LLDP)
                        elif len(an) > 1:                                               #   если узлов несколько
                            sn = []                                                     #   инициализируем переменную b_serial списком серийников с разделителем
                            for i in range (len(an)): 
                              sn.append(l_nodes[an[i]]['serial'])
                            b_serialnum = ", ".join(sn)
                            b_hw = "{} devices".format(len(an))                         #     вместо модели сообщаем количество устройств

                    elif len(l_nodes_i) == 0:                                           # Иначе если таковое устройство в списке не найдено
                        lastnode += 1
                        l_nodes.append({                                                #       добавить новый узел и его параметры в список узлов
                            'id': lastnode,                                             #       (добавляются узлы из данных lldp, они не содержатся в hosts.yaml)
                            'dev': "LLDP_{}".format(b_uuid),
                            'mac': b_uuid,
                            'hostname': b_hostname,
                            'hardware': lnk['system_description'],
                            'serial': lnk['serial'] })        
                        print("New Node:\n Device ID {}: host {} (MAC {}; SN '{}'), HW {}\n".format(     #       вывести на экран параметры добавляемого узла
                            lastnode, 
                            l_nodes[lastnode]['hostname'], 
                            l_nodes[lastnode]['mac'],
                            l_nodes[lastnode]['serial'], 
                            l_nodes[lastnode]['hardware']))
                        devs.append({                                                   #       добавляем элемент соответствия dev_name - id_node - uuid - capablilities      
                            'dev_name': "LLDP_{}".format(b_uuid),
                            'hostname': l_nodes[lastnode]['hostname'],
                            'id_node': lastnode,
                            'uuid': b_uuid,
                            'caps': lnk['capabilities']})
                        b_id = lastnode
                        b_hw = re.sub(r'\,.*$',"",l_nodes[lastnode]['hardware'])        #       инициализируем переменную a_hw (значение слева до первой запятой из поля HW LLDP)
  
                    side_1 = {'id': a_id, 'uuid': a_uuid, 'port': a_port, 'hostname': a_hostname, 'sn': a_serialnum, 'hw': a_hw}        # инициализация словаря для стороны 1 подключения
                    side_2 = {'id': b_id, 'uuid': b_uuid, 'port': b_port, 'hostname': b_hostname, 'sn': b_serialnum, 'hw': b_hw}        # инициализация словаря для стороны 2 подключения

                    if ("{}:{}".format(a_uuid, a_port) >                                # для исключения дублирующихся связей между двумя устройствами (данные 1 связи присутствуют в их LLDP)
                        "{}:{}".format(b_uuid, b_port)):                                # придерживаемся правила - строка "uuid:port" стороны 1 всегда <= "uuid:port" стороны 2
                        side_tmp = side_1; side_1 = side_2; side_2 = side_tmp           # если правило не соблюдается - меняем местами данные side_1 и side_2

                    strRes  = " Side 1: device ID {}; host/port {}:{} (MAC: '{}'; SN: '{}'); info: {}\n".format(
                        side_1['id'], side_1['hostname'], side_1['port'], side_1['uuid'], side_1['sn'], side_1['hw'])
                    strRes += " Side 2: device ID {}; host/port {}:{} (MAC: '{}'; SN: '{}'); info: {}\n".format(
                        side_2['id'], side_2['hostname'], side_2['port'], side_2['uuid'], side_2['sn'], side_2['hw'])                    
                    
                    l_new = ( {'1-id': side_1['id'], "1-host": side_1['hostname'], '1-port': side_1['port'],     # подготовленная структура для добавления в список связей
                               '2-id': side_2['id'], "2-host": side_2['hostname'], '2-port': side_2['port']} )

                    isFound = False                                                     # Проверяем - присутствует ли в списке связей добавляемая связь
                    for i in range (len(l_lines)):                                      # 
                        if l_lines[i] == l_new: isFound = True; break                   #  если совпадение найдено - устанавливаем флаг isFound и досрочно выходим из цикла
                    
                    if isFound:                                                         # Если в списке связей уже есть данные о связи l_new
                        print ("Duplicate Link \n{}".format(strRes))                    #  вывод на экран параметров без добавление в список связей
                    else:                                                               # в противном случае -
                        l_lines.append(l_new)                                           #  добавление l_new в список связей l_lines и вывод на экран параметров 
                        print ("New Link \n{}".format(strRes))

def save_and_diff_topology(str_fname):

    file = open(SCHM_DIR + str_fname + ".yaml", 'w', encoding='utf-8')                  # Засись текущей топологии в файл .yaml
    s_topology = {'devices': devs, 'links': l_lines}
    yaml.dump(s_topology, file)
    file.close()
                                                                                        # АНАЛИЗ ИЗМЕНЕНИЙ с предыдущей топологией
    curr_file = str_fname + ".yaml"                                                     # ищется имя файла предыдущей топологии
    str_head = "topology_"
    topology_files = {}
    for fname in os.listdir(SCHM_DIR):
      if fname.endswith(".yaml") and fname != curr_file:
          fname_datetime = datetime.strptime(fname.strip(".yaml")[len(str_head):],'%Y%m%d-%H%M%S')
          topology_files[fname_datetime.strftime('%Y%m%d%H%M%S')] = fname
    if len(topology_files) > 0:
        prev_topology_file = topology_files [ sorted(topology_files.keys(), reverse = True)[0] ]
        print("Previous topology file: '{}'".format(prev_topology_file))
    else: prev_topology_file = ""

    print("Current topology file : '{}'".format(curr_file))

    if prev_topology_file != "":                                                # Если файл предыдущей топологии существует - приступить к анализу
      file = open(SCHM_DIR + prev_topology_file, 'r', encoding='utf-8')
      p_topology = yaml.safe_load(file) 
      file.close()
     
      print("Analyzing current topology vs previous:")
      fl_dirty = False                                                          # Флаг устанавливается, если обнаружено хотя бы одно изменение

                                                                                # АНАЛИЗ ИЗМЕНЕНИЙ УСТРОЙСТВ

      for i in range (len(s_topology['devices'])):                              # Для каждого устройства из текущей топологии ищется устройство из предыдущей
        is_found = False
        for j in range (len(p_topology['devices'])):
            if s_topology['devices'][i]['uuid'] == p_topology['devices'][j]['uuid']:
                is_found = True
                break
        if is_found == False:                                                   # Если для i-го устройства в текущей топологии нет соответствия в предыдущей - то оно было добавлено
            print(" new device: '{}'".format(s_topology['devices'][i]['dev_name']))
            fl_dirty = True

      for i in range (len(p_topology['devices'])):                              # Для каждого устройства из старой топологии ищется устройство из текущей
        is_found = False
        for j in range (len(s_topology['devices'])):
            if p_topology['devices'][i]['uuid'] == s_topology['devices'][j]['uuid']:
                is_found = True
                break
        if is_found == False:                                                   # Если для i-го устройства в текущей топологии нет соответствия в предыдущей - то оно было удалено
            print(" removed device: '{}'".format(p_topology['devices'][i]['dev_name']))

                                                                                # АНАЛИЗ ИЗМЕНЕНИЙ СВЯЗЕЙ (ЛИНКОВ)

      for i in range (len(s_topology['links'])):                                # Для каждой связи из текущей топологии ищется связь в предыдущей
        is_found = False
        for j in range (len(p_topology['links'])):
            if ((s_topology['links'][i]['1-host'] == p_topology['links'][j]['1-host']) 
                and (s_topology['links'][i]['2-host'] == p_topology['links'][j]['2-host'])
                and (s_topology['links'][i]['1-port'] == p_topology['links'][j]['1-port']) 
                and (s_topology['links'][i]['1-host'] == p_topology['links'][j]['1-host'])):
                is_found = True
                break
        if is_found == False:                                                   # Если для i-го связи в текущей топологии нет соответствия в предыдущей - то она была добавлена
            print(" new link: \n  device '{}' port '{}'\n  device '{}' port '{}'".format(
                s_topology['links'][i]['1-host'], s_topology['links'][i]['1-port'],
                s_topology['links'][i]['2-host'], s_topology['links'][i]['2-port']))
            fl_dirty = True

      for i in range (len(p_topology['links'])):                                # Для каждой связи из предыдущей топологии ищется связь в теущей
        is_found = False
        for j in range (len(s_topology['links'])):
            if ((p_topology['links'][i]['1-host'] == s_topology['links'][j]['1-host']) 
                and (p_topology['links'][i]['2-host'] == s_topology['links'][j]['2-host'])
                and (p_topology['links'][i]['1-port'] == s_topology['links'][j]['1-port']) 
                and (p_topology['links'][i]['1-host'] == s_topology['links'][j]['1-host'])):
               is_found = True
               break
        if is_found == False:                                                   # Если для i-го связи в предыдущей топологии нет соответствия в текущей - то она была удалена
            print(" removed link: \n  device '{}' port '{}'\n  device '{}' port '{}'".format(
                p_topology['links'][i]['1-host'], p_topology['links'][i]['1-port'],
                p_topology['links'][i]['2-host'], p_topology['links'][i]['2-port']))
            fl_dirty = True

      if fl_dirty == False: print(" 100% match")


def draw_topology(str_fname):

    gr = gv.Graph(str_fname, format='svg')
    gr.attr(label = "Graph {}".format(str_fname),
           ranksep = "2", nodesep = "0.4", fontname = "Arial", fontsize = "8pt", rankdir = "LR", size = "20")

    for i in range (len(devs)):
        n_name = devs[i]['hostname']
        
        choises = {'B': '../switch.png',                                                   # Выбор изображения устройства зависит от свойства "capabilities"
                   'R': '../router.png',                                                   
                   'B,R': '../router.png',
                   'B,T': '../phone.png'}
        img = choises.get(devs[i]['caps'], '../switch.png')
        if ("," in devs[i]['uuid']): img = '../coreswitch.png'                             # Если узлов несколько - то это stack

        gr.node(n_name, n_name,
                image = "{}".format(img),
                style="filled", color="transparent", shape="box", labelloc="bottom", imagescale="width", 
                fixedsize="true", fillcolor="transparent", fontcolor="#FF2000", fontsize="7pt", fontname="Arial")

    for i in range (len(l_lines)):
        gr.edge(l_lines[i]['1-host'], l_lines[i]['2-host'],
                taillabel = l_lines[i]['1-port'], headlabel = l_lines[i]['2-port'],
                color ="#9F9F9F", fontcolor = "blue",
                fontname="Arial", fontsize="4pt")

    print("\nRendering file '{}'\n".format(str_fname))
    gr.render(filename = SCHM_DIR + str_fname, quiet = True)

if __name__ == '__main__':
    main()