


The only files that were modified to run the new version of the Brillouin Scan Interface is the following: 
	- pyqt_app.py (this if the file that is run to start the PyQt4 program, not app.py)
	- CMOSthread.py (holds the code for the thread that acquires frames from the pupil camera)
	- EMCCDthread.py (likewise for frames from the EMCCD camera, also holds methods to perform scans)
	- hough_transform.py (pupil detection algorithm)
	- device_init.py (methods to initialize device APIs for Andor and Mako cameras and Zaber motor)


### PyQt4 ###
The initial version of this program was written using the Python GUI framework Tkinter. There are a lot of cases
where the application would crash and return no error. Tkinter is also not very well suited for multithreading. This is why
I moved the program to another crossplatform framework called PyQt which is the python wrapped version of the C framework Qt. 
PyQt is very intuitive to use and very user friendly. While one would have to hard code the layout for all widgets in Tkinter, 
Qt comes installed with a application called QtDesigner which lets users create a layout in a drag-and-drop interface and then
import that layout to Python code. These are resources to better understand how to use PyQt4:
	- http://zetcode.com/gui/pyqt4/ - short tutorial teaching basics, comes with examples
	- doc.qt.io/archives/qt-4.8/ - official documentation for Qt (in C but still very comprehensive)
	- http://pyqt.sourceforge.net/Docs/PyQt4/index.html - another useful reference for PyQt4 from official website

### Signals and Slots ###
Signals and slots refer to the system used by PyQt4 to essentially hook up a widget (i.e. button or numerical entry) to a function
in the backend to call when they are used. The "signal" refers to how a certain widget is used by the user, for example, the signal can be when the button is clicked. The "slot" refers to connecting that signal occurrence to a function that should be called when the signal is made. In qt_ui.py you will find all widgets that are used by the program initialized there.
This file is what is created when we import a layout from QtDesigner (explained above). We then tell the GUI what to do when the
widgets are used (i.e. what happens when a button is pressed). The code that does this is done in pyqt_app.py under mainUI(). Refer to the following to see how signals and slots are set: http://pyqt.sourceforge.net/Docs/PyQt4/old_style_signals_slots.html and the tutorials here: http://zetcode.com/gui/pyqt4/. Also for reference, I ended up using the old style signals not the new style. 

### General Overview ###
This program is multithreaded with 3 distinct threads, the main program thread, CMOS thread which acquires and works with pupil camera images, and the EMCCD thread which acquires and scans with the EMCCD camera. The CMOS and EMCCD threads are defined in separate files (CMOSthread.py and EMCCDthread.py) and initialized in pyqt_app.py which is where the main program thread is defined and initialized. CMOSthread and EMCCDthread are initialied in __init__() of App. CMOSthread and EMCCDthread both have a run()
function which holds the main functionality of acquiring frames from their respective cameras. They need to send these frames to
the main program thread so that the GUI can update what is shown on the CMOS and EMCCD cameras. To accomplish this, we also set 
up a signal:

	self.connect(self.CMOSthread,QtCore.SIGNAL('update_CMOS_panel(PyQt_PyObject)'),self.update_CMOS_panel)

and connected it to the function update_CMOS_panel(). This is the strategy we use for the different threads to communicate
with the main program so that it can update with frames or graph data. 

### Pupil Detection ###
The pupil detection algorithm is located inside hough_transform.py. There is a lot of unnecessary code in that file, but the
only one that I use for the program is the function detect_pupil_frame() which takes a frame as a numpy array along with all the parameters and returns a 3-tuple (frame_bgr,min_circle_center,min_circle_radius). frame_bgr is a BGR colored frame that has the
detected pupil center and radius drawn, min_circle_center is the (x,y) center of the detected pupil, and min_circle_radius is 
the radius of the detected pupil. This function is used in the run() of CMOSthread.py

### Scans ###
Scanning is done in scan() in EMCCDthread. This is a little complicated because it must coordinate moving the motor a set distance
and acquiring a frame from the EMCCD camera after it finishies moving. Given the start_pos, length, and num_scans it first
determines how much the camera moves between each scan (length/num_scans). It moves the camera to start_pos, then takes an initial
frame using the method acquire_frame() and stores it in a export_list. Then it repeats moving the camera then acquiring a frame 
for num_scan times. At the end of the method, it exports all images stored in export_list in addition to the Brillouin frequency
shift averaged over each scan in the profile. 



### Things to do ###
- Figure out how to use HDF5 file format to save scan data in a compressed format (h5py is the python library for this)
- Adding more layers to the heatmap to show BS shift values at different depths over multiple scans
- Finding a good set of parameters for pupil detection, currently I only chose an arbitrary combination that performed decently
- Measuring how long graphing takes (both heatmaps and EMCCD plots) and optimizing, currently I clear the graph then replot 
everything on the next timestep (using fig.clf()), there might be a way to just update the graph without clearing
- Measuring and optimizing all the code, especially the scan() because it take far too long to complete even 20 frames

