import os
import subprocess

from openag_brain.util import pio_build_path, read_module_data
from openag_brain.models import FirmwareModuleTypeModel, FirmwareModuleModel
from openag_brain.db_names import DbName

def download_libs(module_types):
    """
    Downloads the libraries for all of the module_types from platformio
    """
    for module_type_id, module_type in module_types.items():
        subprocess.call(["pio", "lib", "install", module_type.pio_id])

def write_code(modules, module_types, f):
    """
    Writes firmware code to the file given by the file desriptor `f` using the
    modules given by `modules` and the module types given by `module_types`.
    Assumes the libraries for the module types are already installed (i.e.
    `download_libs` was fun.
    """
    # Include the ros library
    f.write("""\
#include <ros.h>

""")

    # Include the headers for the module types
    for module_type in module_types.values():
        f.write("""\
#include <{module_header_file}>
""".format(module_header_file=module_type.header_file))
    f.write('\n')

    # Include the required message types
    msg_types = set()
    msg_types.add("std_msgs/String") # For publishing errors
    for module_type in module_types.values():
        for x in module_type.inputs.values():
            msg_types.add(x)
        for x in module_type.outputs.values():
            msg_types.add(x)
    for msg_type in msg_types:
        f.write("""\
#include <{msg_type}.h>
""".format(msg_type=msg_type))
    f.write('\n')

    # Define all of the modules
    for module in modules.values():
        module_type = module_types[module.type]
        parameters = ", ".join(
            repr(module.parameters[param]) for param in module_type.parameters
        )
        if len(module_type.parameters):
            parameters = "(" + parameters + ")"
        f.write("""\
{mod_cls} {mod_id}{mod_params};
""".format(
            mod_cls=module_type.class_name, mod_id=module.id, mod_params=parameters
        ));
    f.write("\n")

    # Define the ROS node handle
    f.write("""\
ros::NodeHandle nh;

""")

    # Define publishers from module outputs
    publishers = []
    for module in modules.values():
        module_type = module_types[module.type]
        for output, output_type in module_type.outputs.items():
            output_id = module.id + "_" + output
            msg_class = "::".join(output_type.split("/"))
            msg_name = output_id + "_msg"
            pub_name = "pub_" + output_id
            topic_name = "/sensors/" + output_id
            publishers.append(pub_name)
            f.write("""\
{msg_class} {msg_name};
ros::Publisher {pub_name}("{topic_name}", &{msg_name});

""".format(
    msg_class=msg_class, msg_name=msg_name, pub_name=pub_name,
    topic_name=topic_name
))
    f.write("""\
std_msgs::String peripheral_error_msg;
ros::Publisher pub_peripheral_errors("/peripheral_errors", &peripheral_error_msg);

"""
    )

    # TODO: Define subscribers from module inputs

    # Write the setup function
    f.write("""\
void setup() {
  Serial.begin(57600);

  nh.initNode();
""")

    # Register publishers
    for publisher in publishers:
        f.write("""\
  nh.advertise({publisher});
""".format(publisher=publisher))
    f.write("""\
  nh.advertise(pub_peripheral_errors);

""")

    # TODO: Register subscribers

    # Initialize the modules
    for module_id in modules.keys():
        f.write("""\
  {module_id}.begin();
""".format(module_id=module_id))

    f.write("""\
}

""")

    # Write the loop function
    f.write("""\
void loop() {
  nh.spinOnce();
""")
    for module in modules.values():
        module_type = module_types[module.type]
        for output in module_type.outputs.keys():
            output_id = module.id + "_" + output
            msg_name = output_id + "_msg"
            pub_name = "pub_" + output_id
            f.write("""
  if ({mod_id}.get_{output}({msg_name})) {{
    {pub_name}.publish(&{msg_name});
  }}
""".format(
    mod_id=module.id, output=output, msg_name=msg_name, pub_name=pub_name
))
        f.write("""
  if ({mod_id}.has_error) {{
    peripheral_error_msg.data = {mod_id}.error_msg;
    pub_peripheral_errors.publish(&peripheral_error_msg);
    nh.spinOnce();
  }}
""".format(mod_id=module.id))
    f.write("""\
}
""")



def generate_firmware(server):
    """
    Generates firmware based on the configuration read from `server`, which
    should be a `couchdb.Server` instance
    """
    build_folder = pio_build_path()
    src_path = os.path.join(build_folder, "src")
    lib_path = os.path.join(build_folder, "lib")
    if not os.path.isdir(src_path) or not os.path.isdir(lib_path):
        yes = subprocess.Popen(["yes"], stdout=subprocess.PIPE)
        subprocess.call(
            ["pio", "init", "-d", build_folder, "-b", "megaatmega2560"],
            stdin=yes.stdout
        )
        yes.kill()
        yes.wait()
    ros_lib_path = os.path.join(lib_path, "ros_lib")
    if not os.path.isdir(ros_lib_path):
        if subprocess.call([
            "rosrun", "rosserial_arduino", "make_libraries.py", lib_path
        ]):
            import shutil
            shutil.rmtree(ros_lib_path)
            raise RuntimeError("Failed to make rosserial arduino libraries.")

    modules, module_types = read_module_data(
        server[DbName.FIRMWARE_MODULE], FirmwareModuleModel,
        server[DbName.FIRMWARE_MODULE_TYPE], FirmwareModuleTypeModel
    )
    download_libs(module_types)
    src_folder = os.path.join(build_folder, "src")
    sketch_file = os.path.join(src_folder, "src.ino")
    with open(sketch_file, "w+") as f:
        write_code(modules, module_types, f)
